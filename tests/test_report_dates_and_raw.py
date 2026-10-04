import copy
import csv
import gzip
import json
import socket
import io
from datetime import date, timedelta
from email.message import Message
from urllib.request import Request

import pytest

from ihav_competitor_search import homepages
from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.rank import apply_visits
from ihav_competitor_search.render import export


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def table():
    return merge_round(empty_table(), [{"provider": "fake", "raw": {"columns": [], "candidates": [
        {"name": "First", "homepage": "https://first.example", "values": {}},
        {"name": "Second", "homepage": "https://second.example", "values": {}}]}}], 1)


@pytest.mark.parametrize("days,span_expected", [(0, False), (14, False), (15, True), (22, True)])
def test_dates_next_to_values_span_boundary_and_machine_outputs(tmp_path, days, span_expected):
    first = date(2026, 9, 11)
    second = first + timedelta(days=days)
    records = {host: {"exit_code": 0, "result": {"kind": "estimate", "monthly_visits": visits,
        "monthly_visits_text": str(visits), "analyzed_at": str(day)}}
        for host, visits, day in [("first.example", 100, first), ("second.example", 50, second)]}
    ranked = apply_visits(table(), records)
    original = copy.deepcopy(ranked)
    export(ranked, tmp_path)
    assert ranked == original
    assert json.loads((tmp_path / "table.json").read_text()) == original
    for file in ("table.md", "report.html"):
        content = (tmp_path / file).read_text()
        assert "100 (analyzed 2026-09-11)" in content
        assert f"50 (analyzed {second})" in content
        assert ("Traffic estimate dates span" in content) is span_expected
        if span_expected:
            assert content.count("Traffic estimate dates span") == 1
    with (tmp_path / "table.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [row["monthly_visits"] for row in rows] == ["100", "50"]
    assert [row["analyzed_at"] for row in rows] == [str(first), str(second)]
    with (tmp_path / "evidence.csv").open() as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["candidate_id", "column_key", "value", "raw_value", "raw_unit", "source_url", "fetched_at", "method", "verification", "reason"]
        evidence = list(reader)
    assert len(evidence) == 20
    assert all("analyzed" not in row["value"] for row in evidence)
    assert [(r["column_key"], r["value"]) for r in evidence if r["column_key"] == "name"] == [("name", "First"), ("name", "Second")]


def test_display_only_uses_scraped_date_and_stale_marker(tmp_path):
    records = {"first.example": {"exit_code": 0, "result": {"kind": "estimate", "monthly_visits": None,
        "monthly_visits_text": "12.3K", "scraped_at": "2026-10-01", "stale": True, "analyzed_at": "2020-01-01"}},
        "second.example": {"exit_code": 0, "result": {"kind": "estimate", "monthly_visits": 50, "analyzed_at": "2026-10-03"}}}
    ranked = apply_visits(table(), records)
    export(ranked, tmp_path)
    for file in ("table.md", "report.html"):
        content = (tmp_path / file).read_text()
        assert "12.3K (scraped 2026-10-01; stale)" in content
        assert "Traffic estimate dates span" not in content


def response_fixture(monkeypatch, wire, status=200, final_url="https://first.example", headers=None):
    calls = []
    class Response:
        code = status
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return final_url
        def read(self, count):
            assert count == homepages.MAX_BYTES + 1
            return wire[:count]
    response = Response()
    response.headers = headers or Message()
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return response
    monkeypatch.setattr(homepages, "build_opener", lambda handler: Opener())
    return calls


def test_check_saves_filtered_wire_prefix_once(tmp_path, monkeypatch):
    wire = b"<head><title>First</title></head>" + b"x" * (homepages.RAW_BYTES + 100)
    headers = Message()
    for key, value in [("Content-Type", "text/html"), ("Set-Cookie", "private-cookie"),
        ("sEt-CoOkIe", "another-cookie"), ("Authorization", "Bearer private"),
        ("Proxy-Authorization", "private-proxy"), ("X-Auth-Token", "secret"),
        ("Authentication-Info", "secret-info"), ("Cookie", "request-cookie"), ("X-Api-Key", "key"),
        ("ETag", "public-etag")]:
        headers[key] = value
    calls = response_fixture(monkeypatch, wire, headers=headers)
    one = table()
    one["rows"] = one["rows"][:1]
    checks = homepages.check_homepages(one, tmp_path, homepages.HTTPHomepageStage())
    key = one["rows"][0]["candidate_id"]
    assert (tmp_path / "raw" / f"{key}.body").read_bytes() == wire[:65536]
    metadata = json.loads((tmp_path / "raw" / f"{key}.headers.json").read_text())
    assert metadata["http_status"] == 200 and metadata["final_url"] == "https://first.example"
    assert metadata["response_headers"] == [["Content-Type", "text/html"], ["ETag", "public-etag"]]
    assert metadata["saved_wire_bytes"] == 65536
    assert "_raw_evidence" not in checks[key]
    json.dumps(checks)  # Raw bytes do not enter the JSON result contract.
    homepages.check_homepages(one, tmp_path, homepages.HTTPHomepageStage())
    assert len(calls) == 1


def test_gzip_evidence_keeps_wire_bytes_not_decoded_html(tmp_path, monkeypatch):
    wire = gzip.compress(b"<head><title>First</title></head>", mtime=0)
    headers = Message()
    headers["Content-Encoding"] = "gzip"
    response_fixture(monkeypatch, wire, headers=headers)
    one = table()
    one["rows"] = one["rows"][:1]
    checks = homepages.check_homepages(one, tmp_path, homepages.HTTPHomepageStage())
    key = one["rows"][0]["candidate_id"]
    assert checks[key]["title"] == "First"
    assert (tmp_path / "raw" / f"{key}.body").read_bytes() == wire


def test_blocked_response_evidence_is_retained(tmp_path, monkeypatch):
    calls = response_fixture(monkeypatch, b"blocked diagnostic body", status=403)
    one = table()
    one["rows"] = one["rows"][:1]
    checks = homepages.check_homepages(one, tmp_path, homepages.HTTPHomepageStage())
    key = one["rows"][0]["candidate_id"]
    assert checks[key]["reason"] == "blocked"
    assert json.loads((tmp_path / "raw" / f"{key}.headers.json").read_text())["http_status"] == 403
    assert (tmp_path / "raw" / f"{key}.body").read_bytes() == b"blocked diagnostic body"
    assert len(calls) == 1


def test_cross_site_redirect_captures_current_response_without_fetching_target():
    headers = Message()
    headers["Location"] = "https://other.example/"
    headers["Set-Cookie"] = "private"
    response = io.BytesIO(b"redirect response")
    handler = homepages.SameSiteRedirects("https://first.example/")
    with pytest.raises(homepages.RedirectBoundary) as caught:
        handler.redirect_request(Request("https://first.example/"), response, 302, "", headers, "https://other.example/")
    assert caught.value.response_url == "https://first.example/"
    assert caught.value.wire == b"redirect response"
    assert caught.value.headers == [("Location", "https://other.example/")]
    assert response.closed

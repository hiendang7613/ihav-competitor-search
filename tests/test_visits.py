import json
import csv
import socket
import subprocess
from pathlib import Path

import pytest

from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.rank import apply_visits
from ihav_competitor_search.visits import CounterVisitStage, counter_path, lookup_hosts, source_blocked
from ihav_competitor_search.cli import main
from ihav_competitor_search.render import export

FAKE = Path(__file__).parent / "fixtures" / "fake_counter.py"

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

def table(*hosts):
    return merge_round(empty_table(), [{"provider":"test", "raw":{"columns":[],
        "candidates":[{"name":f"Product {i}","homepage":f"https://{host}/product/{i}","values":{}}
                      for i,host in enumerate(hosts)]}}],1)

def calls(project):
    path = project / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

def test_subprocess_dedup_and_exit_groups(tmp_path):
    stage = CounterVisitStage(FAKE,tmp_path)
    t = table("estimate.example","estimate.example","ranked.example","missing.example","failed.example")
    records = lookup_hosts(t,tmp_path,stage)
    assert len(calls(tmp_path)) == 4
    assert {r["lookup_status"] for r in apply_visits(t,records)["rows"]} == {"estimate","rank_only","no_data","lookup_failed"}
    assert records["failed.example"]["exit_code"] == 5
    assert records["estimate.example"]["raw_json"]["monthly_visits"] == 100
    lookup_hosts(t,tmp_path,stage)
    assert len(calls(tmp_path)) == 4
    assert all(c["cwd"] == str(tmp_path) for c in calls(tmp_path))
    assert all(c["cache"] == str(tmp_path/".ihav_space/ihav-web-visit-counter") for c in calls(tmp_path))

def test_stop_block_is_persisted_and_never_retried(tmp_path):
    stage = CounterVisitStage(FAKE,tmp_path)
    t = table("estimate.example","blocked.example","later.example")
    records = lookup_hosts(t,tmp_path,stage)
    assert [c["host"] for c in calls(tmp_path)] == ["estimate.example","blocked.example"]
    assert records["later.example"]["exit_code"] is None
    assert records["later.example"]["visits_unavailable"] == "blocked"
    lookup_hosts(table("new.example"),tmp_path,stage)
    assert len(calls(tmp_path)) == 2

def test_cap_counts_historical_hosts(tmp_path):
    stage = CounterVisitStage(FAKE,tmp_path)
    records = lookup_hosts(table("a.example","b.example"),tmp_path,stage,max_lookups=1)
    assert records["b.example"]["reason"] == "lookup_cap"
    lookup_hosts(table("c.example"),tmp_path,stage,max_lookups=1)
    assert len(calls(tmp_path)) == 1

def test_counter_resolution_and_missing(tmp_path,monkeypatch):
    monkeypatch.delenv("IHAV_VISIT_COUNTER",raising=False)
    with pytest.raises(ValueError,match="restart the host"):
        counter_path()
    monkeypatch.setenv("IHAV_VISIT_COUNTER",str(FAKE))
    assert counter_path() == FAKE.resolve()
    with pytest.raises(ValueError):
        counter_path(tmp_path/"absent.py")

def test_interrupted_dispatch_does_not_retry(tmp_path):
    (tmp_path/"visits.json").write_text(json.dumps({"a.example":{"exit_code":None,"state":"launching"}}))
    records = lookup_hosts(table("a.example","b.example"),tmp_path,CounterVisitStage(FAKE,tmp_path))
    assert records["a.example"]["state"] == "unknown"
    assert records["b.example"]["reason"] == "lookup_outcome_unknown"
    assert calls(tmp_path) == []

def test_timeout_and_lock_stop_without_retry(tmp_path,monkeypatch):
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired(args[0],120)
    monkeypatch.setattr(subprocess,"run",timeout)
    records = lookup_hosts(table("a.example","b.example"),tmp_path,CounterVisitStage(FAKE,tmp_path))
    assert records["a.example"]["state"] == "unknown"
    assert records["b.example"]["reason"] == "lookup_outcome_unknown"
    (tmp_path/"lookup.lock").mkdir()
    with pytest.raises(ValueError,match="lock"):
        lookup_hosts(table("c.example"),tmp_path,CounterVisitStage(FAKE,tmp_path))

def test_unconfirmed_hosts_are_skipped(tmp_path):
    records = lookup_hosts(table("a.example","b.example"),tmp_path,CounterVisitStage(FAKE,tmp_path),eligible_hosts={"a.example"})
    assert len(calls(tmp_path)) == 1
    assert records["b.example"]["reason"] == "homepage_unconfirmed"

def test_lookup_cli_and_offline_render(tmp_path):
    run = tmp_path / ".ihav_space/ihav-competitor-search/runs/test"
    answers = run / "rounds/1/answers"
    answers.mkdir(parents=True)
    t = table("estimate.example","ranked.example")
    candidates = [{"name":r["cells"]["name"]["value"],"homepage":r["cells"]["homepage"]["value"],"values":{}} for r in t["rows"]]
    (answers/"fake.json").write_text(json.dumps({"provider":"fake","raw":{"columns":[],"candidates":candidates}}))
    (run/"request.json").write_text(json.dumps({"homepage_confirmations":{
        "estimate.example":{"status":"confirmed","source_url":"https://estimate.example","fetched_at":"2026-10-03"},
        "ranked.example":{"status":"confirmed","source_url":"https://ranked.example","fetched_at":"2026-10-03"}}}))
    args = ["--project",str(tmp_path)]
    assert main(args+["lookup","test","--counter",str(FAKE)]) == 0
    assert len(calls(tmp_path)) == 2
    assert main(args+["render","test"]) == 0
    assert len(calls(tmp_path)) == 2
    assert main(args+["lookup","test","--counter",str(tmp_path/"absent")]) == 2

def test_malformed_counter_keeps_raw_and_block(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,"run",lambda *a,**k: subprocess.CompletedProcess(a[0],4,"not json","blocked diagnostics"))
    stage = CounterVisitStage(FAKE,tmp_path)
    record = stage.lookup("blocked.example")
    assert record["exit_code"] == 4
    assert record["counter_exit_code"] == 4
    assert record["raw_stdout"] == "not json"
    monkeypatch.setattr(subprocess,"run",lambda *a,**k: subprocess.CompletedProcess(a[0],0,'{"kind":"estimate","domain":"wrong","monthly_visits":100}',""))
    record = stage.lookup("a.example")
    assert record["exit_code"] == 5
    assert record["counter_exit_code"] == 0
    assert record["reason"] == "invalid_counter_response"

def test_successful_fallback_stops_after_primary_block(tmp_path):
    stage = CounterVisitStage(FAKE,tmp_path)
    records = lookup_hosts(table("fallback.example","later.example"),tmp_path,stage)
    assert [c["host"] for c in calls(tmp_path)] == ["fallback.example"]
    assert records["fallback.example"]["exit_code"] == 0
    assert records["fallback.example"]["result"]["monthly_visits"] == 100
    assert records["fallback.example"]["primary_blocked"] is True
    assert records["later.example"]["visits_unavailable"] == "blocked"
    lookup_hosts(table("other.example"),tmp_path,stage)
    assert len(calls(tmp_path)) == 1

def test_note_detection_distinguishes_block_from_general_failure():
    def record(note):
        return {"exit_code":0,"result":{"notes":[note]}}
    assert source_blocked(record("Using this fallback after WebTrafficChecker failed: WebTrafficChecker returned a challenge page; stopped this source without retrying."))
    assert source_blocked(record("WebTrafficChecker failed: WebTrafficChecker returned HTTP 429; stopped this source without retrying."))
    assert not source_blocked(record("WebTrafficChecker failed: WebTrafficChecker returned HTTP 503; no retry was made. No blocked source was retried."))
    assert not source_blocked(record("TrafficLens failed: TrafficLens returned HTTP 403; stopped this source without retrying."))

@pytest.mark.parametrize("host,exit_code,blocked", [
    ("v2-block-fallback.example", 0, True),
    ("v2-block-error.example", 4, True),
    ("v2-cached.example", 0, False),
    ("v2-ok.example", 0, False),
    ("fallback.example", 0, True),
])
def test_counter_contract_versions_stop_and_preserve_raw(tmp_path, host, exit_code, blocked):
    records = lookup_hosts(table(host, "later.example"), tmp_path, CounterVisitStage(FAKE, tmp_path))
    record = records[host]
    assert record["exit_code"] == exit_code
    assert source_blocked(record) is blocked
    assert len(calls(tmp_path)) == (1 if blocked else 2)
    saved = json.loads((tmp_path / "visits.json").read_text())[host]
    assert saved["raw_json"] == record["raw_json"]
    if host.startswith("v2-"):
        assert saved["raw_json"]["contract_version"] == 2
        assert saved["raw_json"]["providers"] == record["raw_json"]["providers"]
    if host.startswith("v2-cached"):
        assert saved["result"]["fetched_at"] == "2026-10-03"
    lookup_hosts(table("new.example"), tmp_path, CounterVisitStage(FAKE, tmp_path))
    assert len(calls(tmp_path)) == (1 if blocked else 3)

def test_v2_uses_any_primary_entry_and_ignores_other_provider_blocks():
    payload = {"contract_version": 2, "providers": [
        {"name": "trafficlens", "outcome": "blocked"},
        {"name": "webtrafficchecker", "outcome": "cached"}]}
    record = {"exit_code": 0, "primary_blocked": True, "raw_json": payload}
    assert not source_blocked(record)
    payload["providers"].append({"name": "webtrafficchecker", "outcome": "blocked"})
    assert source_blocked(record)

def test_exit_two_preserves_error_notes(tmp_path,monkeypatch):
    payload = {"error":{"message":"No primary data","notes":["TrafficLens failed: synthetic network error"]}}
    monkeypatch.setattr(subprocess,"run",lambda *a,**k: subprocess.CompletedProcess(a[0],2,json.dumps(payload),""))
    record = CounterVisitStage(FAKE,tmp_path).lookup("a.example")
    assert record["exit_code"] == 2
    assert record["raw_json"]["error"]["notes"] == payload["error"]["notes"]

def test_display_only_estimates_preserve_fields_and_sort(tmp_path):
    t = table("display.example","numeric.example","shared.example","shared.example","display-shared.example","display-shared.example")
    stage = CounterVisitStage(FAKE,tmp_path)
    records = lookup_hosts(t,tmp_path,stage)
    ranked = apply_visits(t,records)
    assert [r["lookup_host"] for r in ranked["rows"]] == [
        "numeric.example","display.example","shared.example","shared.example","display-shared.example","display-shared.example"]
    for row in ranked["rows"]:
        if row["lookup_host"].startswith("display"):
            assert row["lookup_status"] == "estimate"
            assert row["rank_basis"] is None and row["traffic_rank"] is None
            assert row["traffic"]["result"]["monthly_visits"] is None
    export(ranked,tmp_path)
    with (tmp_path/"table.csv").open() as handle:
        flat = list(csv.DictReader(handle))
    display = next(r for r in flat if r["lookup_host"] == "display.example")
    assert display["monthly_visits"] == ""
    assert display["monthly_visits_text"] == "12.3K"
    assert display["stale"] == "true" and display["scraped_at"] == "2026-09-01"
    assert display["traffic_rank"] == "" and display["rank_basis"] == ""
    for name in ("table.md","report.html"):
        content = (tmp_path/name).read_text()
        assert "12.3K" in content and "2026-09-01" in content

def test_cli_confirmation_gate_accepts_only_recorded_matching_host(tmp_path):
    run = tmp_path/".ihav_space/ihav-competitor-search/runs/gate"
    answers = run/"rounds/1/answers"
    answers.mkdir(parents=True)
    candidates = [{"name":host,"homepage":f"https://{host}","values":{}}
                  for host in ["display.example","unconfirmed.example","wrong.example","undated.example"]]
    (answers/"fake.json").write_text(json.dumps({"provider":"fake","raw":{"columns":[],"candidates":candidates}}))
    (run/"request.json").write_text(json.dumps({"homepage_confirmations":{
        "display.example":{"status":"confirmed","source_url":"https://www.display.example","fetched_at":"2026-10-03"},
        "wrong.example":{"status":"confirmed","source_url":"https://another.example","fetched_at":"2026-10-03"},
        "undated.example":{"status":"confirmed","source_url":"https://undated.example"}}}))
    args=["--project",str(tmp_path)]
    assert main(args+["lookup","gate","--counter",str(FAKE)]) == 0
    assert [c["host"] for c in calls(tmp_path)] == ["display.example"]
    records=json.loads((run/"visits.json").read_text())
    assert all(records[h]["reason"] == "homepage_unconfirmed" for h in ["unconfirmed.example","wrong.example","undated.example"])
    assert main(args+["render","gate"]) == 0
    assert len(calls(tmp_path)) == 1
    rendered=json.loads((run/"table.json").read_text())
    display=next(r for r in rendered["rows"] if r["lookup_host"] == "display.example")
    assert display["traffic_rank"] is None

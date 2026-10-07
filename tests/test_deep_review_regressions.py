"""Concrete review counterexamples at merge, traffic and dispatch boundaries."""
import csv
import io
import json
import socket
import subprocess
from pathlib import Path

import pytest

from ihav_competitor_search.cli import main
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.rank import apply_visits
from ihav_competitor_search.render import export, flat_row
from ihav_competitor_search.survey import create_run
from ihav_competitor_search.visits import CounterVisitStage, lookup_hosts, source_blocked


PRICE = {"key": "price", "meaning": "monthly price", "type": "number", "unit": "USD/month"}
FAKE_COUNTER = Path(__file__).parent / "fixtures/fake_counter.py"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def answer(name, values=None, columns=()):
    return {"provider": "fixture", "raw": {"columns": list(columns), "candidates": [
        {"name": name, "homepage": "https://" + name.lower() + ".example", "values": values or {}}]}}


def initial():
    return merge_round(empty_table(), [answer("Alpha", {"price": 19}, [PRICE])], 1)


@pytest.mark.parametrize("budgets", [
    {"max_new_columns": 0}, {"max_total_columns": 11},
], ids=["round-cap", "total-cap"])
def test_rejected_redeclaration_never_relabels_a_value(tmp_path, budgets):
    annual = {**PRICE, "meaning": "annual price", "unit": "EUR/year"}
    result = merge_round(initial(), [answer("Beta", {"price": 240}, [annual])], 2, **budgets)
    beta = result["rows"][1]
    assert beta["cells"]["price"]["value"] is None
    assert beta["observations"][0]["raw"]["values"] == {"price": 240}
    assert any(i["reason"] == "column_budget" and i["key"] == "price" for i in result["issues"])
    export(apply_visits(result, {}), tmp_path)
    exported = list(csv.DictReader(io.StringIO((tmp_path / "table.csv").read_text())))
    assert next(r for r in exported if r["name"] == "Beta")["price"] == ""


def test_invalid_declaration_cannot_fall_through_to_an_accepted_key():
    result = merge_round(initial(), [answer("Beta", {"price": 240}, [{**PRICE, "type": "invalid"}])], 2)
    assert result["rows"][1]["cells"]["price"]["value"] is None
    assert any(i["reason"] == "invalid_column" for i in result["issues"])


@pytest.mark.parametrize("alias_value", [99, True], ids=["unequal-number", "bool-not-number"])
def test_alias_collision_does_not_silently_choose_a_value(alias_value):
    direct = 1 if alias_value is True else 12
    values = {"price": direct, "monthly_cost": alias_value}
    result = merge_round(initial(), [answer("Beta", values, [{**PRICE, "key": "monthly_cost"}])], 2)
    beta = result["rows"][1]
    assert beta["cells"]["price"]["value"] is None
    assert beta["cells"]["price"]["reason"] == "conflicting_values"
    assert beta["observations"][0]["raw"]["values"] == values
    assert any(i["reason"] == "conflicting_values" and i["column_key"] == "price"
               for i in result["issues"])


@pytest.mark.parametrize("values", [{"price": 12}, {"monthly_cost": 12},
                                   {"price": 12, "monthly_cost": 12}])
def test_unambiguous_accepted_and_alias_keys_still_work(values):
    result = merge_round(initial(), [answer("Beta", values, [{**PRICE, "key": "monthly_cost"}])], 2)
    assert result["rows"][1]["cells"]["price"]["value"] == 12
    assert not any(i["reason"] == "conflicting_values" for i in result["issues"])


@pytest.mark.parametrize("exit_code", [None, 2, 4, 5, 64])
def test_saved_failed_traffic_result_refuses_without_replacing_outputs(tmp_path, exit_code):
    run = create_run(tmp_path, "OCR", run_id="traffic-outcome")
    import_answer(run, 1, "fixture", json.dumps(answer("Alpha")["raw"]))
    (run / "visits.json").write_text(json.dumps({"alpha.example": {"exit_code": exit_code,
        "result": {"kind": "estimate", "monthly_visits": 999}}}))
    outputs = ("table.json", "table.csv", "evidence.csv", "table.md", "report.html")
    for name in outputs:
        (run / name).write_text("previous " + name)
    assert main(["--project", str(tmp_path), "render", run.name]) == 2
    assert {name: (run / name).read_text() for name in outputs} == {
        name: "previous " + name for name in outputs}


def test_rank_only_visit_text_is_rejected_at_child_and_saved_boundaries(tmp_path, monkeypatch):
    payload = {"domain": "alpha.example", "kind": "rank_only", "monthly_visits": None,
               "monthly_visits_text": "12.3K", "rank": {"value": 7}}
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, json.dumps(payload), ""))
    record = CounterVisitStage(FAKE_COUNTER, tmp_path).lookup("alpha.example")
    assert record["exit_code"] == 5
    assert "result" not in record
    assert record["raw_json"] == payload
    with pytest.raises(ValueError, match="rank_only"):
        apply_visits(merge_round(empty_table(), [answer("Alpha")], 1), {
            "alpha.example": {"exit_code": 0, "result": payload}})


@pytest.mark.parametrize("status,exit_code,result", [
    ("no_data", 2, {"kind": "estimate", "monthly_visits": 999}),
    ("rank_only", 0, {"kind": "rank_only", "monthly_visits": None,
                       "monthly_visits_text": "12.3K", "rank": {"value": 7}}),
])
def test_flat_projection_does_not_publish_invalid_visit_fields(status, exit_code, result):
    table = apply_visits(merge_round(empty_table(), [answer("Alpha")], 1), {})
    row = table["rows"][0]
    row.update(lookup_status=status, traffic={"exit_code": exit_code, "result": result})
    flat = flat_row(row, table["columns"])
    assert flat["monthly_visits"] == ""
    assert flat["monthly_visits_text"] == ""
    if status == "no_data":
        assert flat["traffic_kind"] == ""
    else:
        assert flat["tranco_rank"] == "7"


@pytest.mark.parametrize("payload", [
    {"contract_version": 2}, {"contract_version": 2, "providers": []},
    {"contract_version": 2, "providers": "corrupt"},
    {"contract_version": 2, "providers": [{"name": "trafficlens", "outcome": "failed"}]},
], ids=["absent", "empty", "non-list", "missing-primary"])
def test_malformed_v2_cannot_override_authoritative_block(tmp_path, monkeypatch, payload):
    invocations = []
    def completed(args, **kwargs):
        host = args[2]
        invocations.append(host)
        if host == "alpha.example":
            return subprocess.CompletedProcess(args, 4, json.dumps(payload), "blocked diagnostics")
        return subprocess.CompletedProcess(args, 0, json.dumps({"domain": host, "kind": "estimate",
                                                               "monthly_visits": 10}), "")
    monkeypatch.setattr(subprocess, "run", completed)
    table = merge_round(empty_table(), [answer("Alpha"), answer("Beta")], 1)
    stage = CounterVisitStage(FAKE_COUNTER, tmp_path)
    records = lookup_hosts(table, tmp_path, stage)
    assert records["alpha.example"]["primary_blocked"] is True
    assert source_blocked(records["alpha.example"]) is True
    assert records["alpha.example"]["raw_json"] == payload
    assert records["alpha.example"]["raw_stderr"] == "blocked diagnostics"
    assert records["beta.example"]["state"] == "skipped"
    assert records["beta.example"]["reason"] == "blocked"
    lookup_hosts(merge_round(empty_table(), [answer("Gamma")], 1), tmp_path,
                 CounterVisitStage(FAKE_COUNTER, tmp_path))
    assert invocations == ["alpha.example"]


def test_current_v2_outcomes_override_stale_legacy_block_flag():
    cached = {"exit_code": 0, "primary_blocked": True, "raw_json": {"contract_version": 2,
              "providers": [{"name": "trafficlens", "outcome": "blocked"},
                            {"name": "webtrafficchecker", "outcome": "cached"}]}}
    assert source_blocked(cached) is False
    fallback = {"exit_code": 0, "raw_json": {"contract_version": 2, "providers": [
        {"name": "webtrafficchecker", "outcome": "blocked"}, {"name": "trafficlens", "outcome": "ok"}]}}
    assert source_blocked(fallback) is True

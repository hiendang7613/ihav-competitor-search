"""Overall mode coverage must include every recorded round, including later intent.

All child calls use a copied synthetic fixture; none starts a worker or provider.
"""
import copy
import hashlib
import html
import json
import os
import re
import socket
from pathlib import Path

import pytest

from ihav_competitor_search import research
from ihav_competitor_search import synthesis as synthesis_module
from ihav_competitor_search.chatbots import WebChatChild, ask, collect, read, save
from ihav_competitor_search.cli import main
from ihav_competitor_search.render import export
from ihav_competitor_search.survey import create_run, progress
from ihav_competitor_search.synthesis import synthesize

FAKE = Path(__file__).parent / "fixtures/fake_research_web_chat.py"
PROVIDERS = ["synthetic_alpha", "synthetic_beta"]
TARGETS = [{"provider": p, "kind": "research", "mode_id": "synthetic-research-v1",
            "settings": {"depth": "synthetic", "strict": True}} for p in PROVIDERS]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network/provider calls forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic-coverage-session")
    expected = os.environ.get("RESEARCH_REVIEW_SOURCE")
    if expected:
        assert Path(synthesis_module.__file__).resolve().is_relative_to(Path(expected).resolve()), "wrong source under test"


def answer_path(directory, number, provider):
    return directory / "rounds" / str(number) / "answers" / (provider + ".json")


def set_config(project, **values):
    save(project / "synthetic-chat-config.json", values)


def record_synthetic_homepage_decisions(directory, table):
    request = read(directory / "request.json")
    request["homepage_decisions"] = {r["candidate_id"]: {"status": "unconfirmed", "reason": "synthetic fixture"}
                                    for r in table["rows"]}
    save(directory / "request.json", request)


def first_two(project, *, budget=3, no_progress=False):
    directory = create_run(project, "Synthetic coverage regression", run_id="coverage", targets=copy.deepcopy(TARGETS),
                           rounds=budget, max_lookups=0, no_verify=True)
    child = WebChatChild(FAKE, project)
    for number in (1, 2):
        prior = synthesize(directory, through_round=number - 1)[0] if number > 1 else None
        ask(directory, child, number, opt_in=number == 1, table=prior)
        if number == 2 and no_progress:
            set_config(project, answers={p: read(answer_path(directory, 1, p))["raw"] for p in PROVIDERS})
        collect(directory, child, number)
        table, _ = synthesize(directory)
        record_synthetic_homepage_decisions(directory, table)
    return directory, child


def assert_exports(directory, table, *, met):
    export(table, directory)
    json_summary = read(directory / "table.json")["research"]
    lines = (directory / "table.md").read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("Research mode evidence"))
    markdown_summary = json.loads(html.unescape(lines[index + 1]))
    report = (directory / "report.html").read_text()
    match = re.search(r"<summary>Recorded rounds and provider outcomes</summary><pre>(.*?)</pre>", report, re.S)
    html_summary = json.loads(html.unescape(match[1]))["research"]
    assert json_summary == markdown_summary == html_summary == table["research"]
    assert json_summary["requirements_met"] is met
    assert json_summary["state"] == ("observed" if met else "unverified")


@pytest.mark.parametrize("receipt", ["missing", "mismatch"])
def test_later_unqualified_receipt_invalidates_overall_progress_and_all_summaries(tmp_path, capsys, receipt):
    directory, child = first_two(tmp_path)
    before, _ = synthesize(directory)
    ask(directory, child, 3, table=before)
    if receipt == "missing":
        set_config(tmp_path, missing_receipts=[PROVIDERS[0]])
    else:
        different = copy.deepcopy(TARGETS[0])
        different["settings"]["strict"] = 1
        set_config(tmp_path, receipt_overrides={PROVIDERS[0]: {"observed": different}})
    collect(directory, child, 3)
    table, _ = synthesize(directory)
    record_synthetic_homepage_decisions(directory, table)
    table, _ = synthesize(directory)
    assert [table["research"]["rounds"][str(n)]["requirements_met"] for n in (1, 2, 3)] == [True, True, False]
    assert table["research"]["observed_rounds"] == [1, 2]
    assert progress(directory, table)["state"] == "research_mode_unverified"
    assert_exports(directory, table, met=False)
    assert main(["--project", str(tmp_path), "status", directory.name]) == 0
    assert json.loads(capsys.readouterr().out)["survey"]["research"]["requirements_met"] is False
    assert main(["--project", str(tmp_path), "render", directory.name]) == 0
    assert json.loads(capsys.readouterr().out)["survey"]["research"]["requirements_met"] is False
    assert read(directory / "table.json")["research"]["requirements_met"] is False


@pytest.mark.parametrize("later", ["pending", "unknown", "waiting", "reconciliation"])
def test_unanswered_or_unresolved_later_launch_blocks_overall_research_pass(tmp_path, later):
    directory, child = first_two(tmp_path)
    table, _ = synthesize(directory)
    if later == "unknown":
        set_config(tmp_path, lost_response=True)
        with pytest.raises(ValueError, match="unknown"):
            ask(directory, child, 3, table=table)
    else:
        ask(directory, child, 3, table=table)
        if later == "waiting":
            set_config(tmp_path, statuses={p: "running" for p in PROVIDERS})
            collect(directory, child, 3)
        elif later == "reconciliation":
            collect(directory, child, 3)
            original = read(answer_path(directory, 3, PROVIDERS[0]))
            changed = dict(original["execution_receipt"]["turn_binding"], turn=2)
            set_config(tmp_path, receipt_overrides={PROVIDERS[0]: {"turn_binding": changed}})
            with pytest.raises(ValueError, match="receipt changed"):
                collect(directory, child, 3)
    table, _ = synthesize(directory)
    record_synthetic_homepage_decisions(directory, table)
    table, _ = synthesize(directory)
    assert table["research"]["observed_rounds"] == [1, 2]
    assert "3" in table["research"]["rounds"]
    expected = "unknown" if later == "unknown" else "research_attribution_unresolved" if later == "reconciliation" else "awaiting_child"
    assert progress(directory, table)["state"] == expected
    assert_exports(directory, table, met=False)


def test_no_progress_merge_break_cannot_hide_a_later_recorded_launch(tmp_path):
    directory, child = first_two(tmp_path)
    prior, _ = synthesize(directory)
    ask(directory, child, 3, table=prior)
    # Source-valid historical fixture: later intent exists after a predecessor
    # stops. These are synthetic files only, not an API overwrite or send replay.
    for p in PROVIDERS:
        earlier = read(answer_path(directory, 1, p))
        second = read(answer_path(directory, 2, p))
        second["raw"] = earlier["raw"]
        second["raw_sha256"] = hashlib.sha256(second["raw"].encode("utf-8")).hexdigest()
        second["execution_receipt"]["raw_answer_sha256"] = second["raw_sha256"]
        second["execution_receipt_sha256"] = research.digest(second["execution_receipt"])
        save(answer_path(directory, 2, p), second)
    table, _ = synthesize(directory)
    assert [summary["round"] for summary in table["rounds"]] == [1, 2]
    assert table["rounds"][-1]["stop"]
    assert table["research"]["observed_rounds"] == [1, 2]
    assert "3" in table["research"]["rounds"]
    assert not progress(directory, table)["research"]["requirements_met"]
    assert_exports(directory, table, met=False)


def test_recorded_gap_and_orphan_collection_cannot_establish_consecutive_coverage(tmp_path):
    directory, _ = first_two(tmp_path, budget=5)
    folder = directory / "rounds/4"
    folder.mkdir()
    save(folder / "children.json", {"state": "waiting", "providers": {}, "child_run_id": "synthetic-orphan"})
    table, _ = synthesize(directory)
    assert table["research"]["observed_rounds"] == [1, 2]
    assert "4" in table["research"]["rounds"]
    assert_exports(directory, table, met=False)


@pytest.mark.parametrize("empty_folder", [False, True])
def test_two_successful_rounds_do_not_claim_unsent_configured_rounds(tmp_path, empty_folder):
    directory, _ = first_two(tmp_path, budget=5)
    if empty_folder:
        (directory / "rounds/3").mkdir()
    table, _ = synthesize(directory)
    assert table["research"]["observed_rounds"] == [1, 2]
    assert set(table["research"]["rounds"]) == {"1", "2"}
    assert progress(directory, table)["state"] == "ready_to_queue"
    assert progress(directory, table)["round"] == 3
    assert_exports(directory, table, met=True)


def test_truthful_early_no_progress_with_no_later_artifacts_is_preserved(tmp_path):
    directory, child = first_two(tmp_path, budget=5, no_progress=True)
    table, _ = synthesize(directory)
    assert table["rounds"][-1]["stop"] and table["research"]["observed_rounds"] == [1, 2]
    assert progress(directory, table)["state"] == "completed_unverified"
    assert_exports(directory, table, met=True)
    with pytest.raises(ValueError, match="stopped the loop"):
        ask(directory, child, 3, table=table)


def test_explicit_prior_projection_can_exclude_later_pending_intent(tmp_path):
    directory, child = first_two(tmp_path)
    table, _ = synthesize(directory)
    ask(directory, child, 3, table=table)
    earlier, _ = synthesize(directory, through_round=2)
    complete, _ = synthesize(directory)
    assert set(earlier["research"]["rounds"]) == {"1", "2"}
    assert earlier["research"]["requirements_met"] is True
    assert complete["research"]["requirements_met"] is False

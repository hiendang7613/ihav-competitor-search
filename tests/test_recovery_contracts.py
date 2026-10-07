"""Regression checks for bounded input and recoverable saved-run contracts."""
import json
from pathlib import Path

import pytest

from ihav_competitor_search.chatbots import WebChatChild, ask, collect
from ihav_competitor_search.cli import main, synthesize
from ihav_competitor_search.homepages import confirm_homepage
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.survey import create_run, progress

FAKE = Path(__file__).parent / "fixtures" / "fake_web_chat.py"


def answer(*names):
    return {"columns": [], "candidates": [
        {"name": name, "homepage": f"https://{name.lower()}.example", "values": {}}
        for name in names]}


def write_config(project, **values):
    (project / "fake-chat-config.json").write_text(json.dumps(values))


def sent_calls(project):
    return [json.loads(line) for line in (project / "chat-calls.jsonl").read_text().splitlines()
            if json.loads(line)[0] == "run" and json.loads(line)[1] != "lookup"]


def test_saved_candidate_budget_is_enforced_per_provider(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="bounded", rounds=1, max_new=1)
    import_answer(run, 1, "alpha", json.dumps(answer("A", "B", "C")))
    import_answer(run, 1, "beta", json.dumps(answer("D", "E", "A")))
    table, _ = synthesize(run)
    assert [row["cells"]["name"]["value"] for row in table["rows"]] == ["A", "D"]
    assert [issue["provider"] for issue in table["issues"]
            if issue["reason"] == "candidate_budget"] == ["alpha", "alpha", "beta"]
    assert table["rows"][0]["mentioned_by"] == [
        {"provider": "alpha", "round": 1, "method": "manual_paste"},
        {"provider": "beta", "round": 1, "method": "manual_paste"}]


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_invalid_candidate_budget_fails_before_merge(budget):
    with pytest.raises(ValueError, match="candidate budget"):
        merge_round(empty_table(), [{"provider": "fixture", "raw": answer("A")}], 1,
                    max_new=budget)


def test_terminal_unconfirmed_homepage_can_finish_with_truthful_evidence(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="unconfirmed", rounds=1, max_lookups=0)
    import_answer(run, 1, "manual", json.dumps(answer("A")))
    table, _ = synthesize(run)
    candidate = table["rows"][0]["candidate_id"]
    confirm_homepage(table, run, candidate, reason="official homepage could not be established")
    updated, _ = synthesize(run)
    state = progress(run, updated)
    assert state["state"] == "completed_unverified" and state["action"] == "render"
    assert state["reason"] == "unconfirmed_homepages"
    assert state["unverified_candidate_ids"] == [candidate]
    assert updated["rows"][0]["verification_status"] == "unverified"


def test_confirmed_unchecked_candidate_still_needs_verification(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="checkable", rounds=1, max_lookups=0)
    import_answer(run, 1, "manual", json.dumps(answer("A", "B")))
    table, _ = synthesize(run)
    confirmed, unconfirmed = [row["candidate_id"] for row in table["rows"]]
    confirm_homepage(table, run, confirmed, "https://a.example", method="official_search")
    confirm_homepage(table, run, unconfirmed, reason="unconfirmed product")
    updated, _ = synthesize(run)
    state = progress(run, updated)
    assert state["state"] == "verification_pending"
    assert state["candidate_ids"] == [confirmed]
    assert state["unverified_candidate_ids"] == [unconfirmed]


@pytest.mark.parametrize("changed", [
    {"provider": "other"}, {"method": "invented"},
    {"status": "parse_failed"}, {"fetched_at": ""}, {"method": []}, {"status": []},
    {"fetched_at": "not-a-timestamp"}, {"fetched_at": "2026-10-06"},
    {"fetched_at": "2026-10-06T00:00:00"},
])
def test_synthesis_rejects_changed_answer_provenance_before_output(tmp_path, changed):
    run = create_run(tmp_path, "fixture", run_id="provenance", rounds=1)
    record = import_answer(run, 1, "chatgpt", json.dumps(answer("A")))
    record.update(changed)
    (run / "rounds/1/answers/chatgpt.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="answer"):
        synthesize(run)
    assert not (run / "table.json").exists()


def test_supported_legacy_answer_keeps_its_saved_identity(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="legacy", rounds=1)
    folder = run / "rounds/1/answers"
    folder.mkdir(parents=True)
    (folder / "legacy.json").write_text(json.dumps({"provider": "legacy", "raw": answer("A")}))
    table, _ = synthesize(run)
    assert table["rows"][0]["cells"]["name"]["source_url"] == "chatbot:legacy:r1"


@pytest.mark.parametrize("launch", [{"state": "launched"}, {}, None])
def test_saved_manual_answer_cannot_be_mixed_with_child_intent(tmp_path, launch):
    run = create_run(tmp_path, "fixture", run_id="mixed", rounds=1)
    import_answer(run, 1, "manual", json.dumps(answer("A")))
    (run / "rounds/1/launch.json").write_text(json.dumps(launch))
    before = (run / "rounds/1/answers/manual.json").read_bytes()
    with pytest.raises(ValueError, match="saved answer"):
        synthesize(run)
    assert (run / "rounds/1/answers/manual.json").read_bytes() == before
    assert not (run / "table.json").exists()


def test_future_manual_round_is_refused_without_writing(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="gap", rounds=2)
    before = (run / "request.json").read_bytes()
    with pytest.raises(ValueError, match="previous round"):
        import_answer(run, 2, "manual", json.dumps(answer("B")))
    assert not (run / "rounds/2").exists()
    assert not (run / "ask.lock").exists()
    assert (run / "request.json").read_bytes() == before


@pytest.mark.parametrize("replace", [False, True])
def test_earlier_manual_round_is_frozen_after_later_answers(tmp_path, replace):
    run = create_run(tmp_path, "fixture", run_id="chain", rounds=2)
    import_answer(run, 1, "manual", json.dumps(answer("A")))
    import_answer(run, 2, "manual", json.dumps(answer("B")))
    original = (run / "rounds/1/answers/manual.json").read_bytes()
    provider = "manual" if replace else "second"
    with pytest.raises(ValueError, match="later round"):
        import_answer(run, 1, provider, json.dumps(answer("C")), replace=replace)
    assert (run / "rounds/1/answers/manual.json").read_bytes() == original
    assert not (run / "rounds/1/answers/second.json").exists()
    table, _ = synthesize(run)
    assert [row["cells"]["name"]["value"] for row in table["rows"]] == ["A", "B"]


@pytest.mark.parametrize("artifact", ["prompt.md", "launch.json", "children.json"])
def test_earlier_manual_round_is_frozen_after_later_intent(tmp_path, artifact):
    run = create_run(tmp_path, "fixture", run_id="intent", rounds=2)
    import_answer(run, 1, "manual", json.dumps(answer("A")))
    folder = run / "rounds/2"
    folder.mkdir()
    (folder / artifact).write_text("{}")
    with pytest.raises(ValueError, match="later round"):
        import_answer(run, 1, "manual", json.dumps(answer("C")), replace=True)


@pytest.mark.parametrize("current_providers", [["chatgpt", "gemini"], []])
def test_known_child_remains_collectable_after_provider_and_capability_drift(tmp_path, current_providers):
    run = create_run(tmp_path, "fixture", run_id="upgrade", rounds=1, providers="all")
    write_config(tmp_path, providers=["chatgpt"])
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(run, child, opt_in=True)
    write_config(tmp_path, providers=current_providers,
                 commands=["status", "delivery read"])
    result = collect(run, child)
    assert result["state"] == "completed" and result["parsed_answers"] == 1
    assert result["child_run_id"] == launch["child_run_id"]
    assert set(result["providers"]) == {"chatgpt"}
    assert len(sent_calls(tmp_path)) == 1


def test_unknown_child_recovers_saved_provider_set_after_upgrade(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="recover", rounds=1, providers="all")
    write_config(tmp_path, providers=["chatgpt"], lost_response=True)
    child = WebChatChild(FAKE, tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, child, opt_in=True)
    write_config(tmp_path, providers=["chatgpt", "gemini"])
    result = collect(run, child)
    assert result["state"] == "completed" and set(result["providers"]) == {"chatgpt"}
    assert len(sent_calls(tmp_path)) == 1


@pytest.mark.parametrize("admission", ["not_admitted", "unknown"])
def test_unknown_launch_requires_durable_child_admission(tmp_path, admission):
    run = create_run(tmp_path, "fixture", run_id="orphan", rounds=1, providers=["chatgpt"])
    child = WebChatChild(FAKE, tmp_path)
    write_config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, child, opt_in=True)
    write_config(tmp_path, admission=admission)
    with pytest.raises(ValueError, match="unknown"):
        collect(run, child)
    launch = json.loads((run / "rounds/1/launch.json").read_text())
    assert launch["state"] == "unknown" and "child_run_id" not in launch
    assert len(sent_calls(tmp_path)) == 1
    assert not (run / "rounds/1/answers").exists()


@pytest.mark.parametrize("changed", [
    {"admission_digest": "0" * 64}, {"admission_schema_version": True},
])
def test_unknown_launch_rejects_unbound_admission_proof(tmp_path, changed):
    run = create_run(tmp_path, "fixture", run_id="corrupt", rounds=1, providers=["chatgpt"])
    child = WebChatChild(FAKE, tmp_path)
    write_config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, child, opt_in=True)
    write_config(tmp_path, **changed)
    with pytest.raises(ValueError, match="unknown"):
        collect(run, child)
    assert len(sent_calls(tmp_path)) == 1


def test_structured_pre_admission_refusal_is_not_unknown_and_never_replayed(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="refused", rounds=1, providers=["chatgpt"])
    child = WebChatChild(FAKE, tmp_path)
    refusal = {"outcome": "refused", "phase": "pre_admission", "effect": "none",
               "code": "prompt_too_long", "message": "prompt exceeds the provider limit"}
    write_config(tmp_path, refusal=refusal)
    with pytest.raises(ValueError, match="refused before queue admission"):
        ask(run, child, opt_in=True)
    launch = json.loads((run / "rounds/1/launch.json").read_text())
    assert launch["state"] == "refused" and launch["reason"] == "prompt_too_long"
    assert launch["refusal"] == refusal
    assert not (tmp_path / ".ihav_space/ihav-web-chat/runs").exists()
    state = progress(run, synthesize(run)[0])
    assert state["state"] == "launch_refused" and state["action"] == "new_run"
    before = (tmp_path / "chat-calls.jsonl").read_bytes()
    write_config(tmp_path)
    with pytest.raises(ValueError, match="refused before queue admission"):
        ask(run, child, opt_in=True)
    assert (tmp_path / "chat-calls.jsonl").read_bytes() == before
    assert len(sent_calls(tmp_path)) == 1


@pytest.mark.parametrize("changed", [{"effect": "unknown"}, {"phase": "admission"}, {"code": []}, {"message": "\ud800"}])
def test_exit_two_without_valid_no_effect_proof_remains_unknown(tmp_path, changed):
    run = create_run(tmp_path, "fixture", run_id="uncertain", rounds=1, providers=["chatgpt"])
    refusal = {"outcome": "refused", "phase": "pre_admission", "effect": "none",
               "code": "prompt_too_long", "message": "refused"}
    refusal.update(changed)
    write_config(tmp_path, refusal=refusal)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert json.loads((run / "rounds/1/launch.json").read_text())["state"] == "unknown"


def test_new_dispatch_requires_versioned_recovery_contract(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="legacy-child", rounds=1, providers=["chatgpt"])
    write_config(tmp_path, contracts={})
    with pytest.raises(ValueError, match="versioned recovery contracts"):
        ask(run, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert not (run / "rounds/1/launch.json").exists()
    assert "outbound_consent" not in json.loads((run / "request.json").read_text())
    assert not sent_calls(tmp_path)


def test_saved_state_selects_dispatch_and_lookup_after_default_drift(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="state-drift", rounds=1, providers=["chatgpt"])
    original = str(tmp_path / "original-state")
    write_config(tmp_path, state_dir=original, lost_response=True)
    child = WebChatChild(FAKE, tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, child, opt_in=True)
    write_config(tmp_path, state_dir=str(tmp_path / "upgraded-default-state"))
    assert collect(run, child)["state"] == "completed"
    calls = [json.loads(line) for line in (tmp_path / "chat-calls.jsonl").read_text().splitlines()]
    launch = sent_calls(tmp_path)[0]
    lookup = next(args for args in calls if args[:2] == ["run", "lookup"])
    assert launch[launch.index("--state-dir") + 1] == original
    assert lookup[lookup.index("--state-dir") + 1] == original
    assert len(sent_calls(tmp_path)) == 1


def test_successful_child_reply_must_match_the_explicit_state_binding(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="wrong-state", rounds=1, providers=["chatgpt"])
    write_config(tmp_path, returned_state_dir=str(tmp_path / "wrong-state"))
    child = WebChatChild(FAKE, tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, child, opt_in=True)
    launch = json.loads((run / "rounds/1/launch.json").read_text())
    assert launch["state"] == "unknown" and "child_run_id" not in launch
    write_config(tmp_path)
    assert collect(run, child)["state"] == "completed"
    assert len(sent_calls(tmp_path)) == 1


def test_legacy_unknown_launch_without_state_binding_does_not_guess(tmp_path):
    run = create_run(tmp_path, "fixture", run_id="legacy-unknown", rounds=1, providers=["chatgpt"])
    write_config(tmp_path, lost_response=True)
    child = WebChatChild(FAKE, tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        ask(run, child, opt_in=True)
    path = run / "rounds/1/launch.json"
    launch = json.loads(path.read_text())
    launch.pop("child_state_dir")
    path.write_text(json.dumps(launch))
    write_config(tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        collect(run, child)
    calls = [json.loads(line) for line in (tmp_path / "chat-calls.jsonl").read_text().splitlines()]
    assert not any(args[:2] == ["run", "lookup"] for args in calls)
    assert len(sent_calls(tmp_path)) == 1


@pytest.mark.parametrize("dry_run", [True, False])
def test_legacy_child_can_preview_new_survey_without_dispatch_contracts(tmp_path, capsys, dry_run):
    write_config(tmp_path, contracts={})
    args = ["--project", str(tmp_path), "survey", "fixture", "--providers", "all", "--web-chat", str(FAKE)]
    assert main(args + (["--dry-run"] if dry_run else [])) == (0 if dry_run else 2)
    result = json.loads(capsys.readouterr().out)
    assert result["preview"]["providers"] == ["chatgpt", "gemini", "perplexity"]
    assert not (tmp_path / ".ihav_space").exists() and not sent_calls(tmp_path)
    assert main(args + ["--opt-in"]) == 2
    assert "versioned recovery contracts" in capsys.readouterr().err
    assert not (tmp_path / ".ihav_space").exists() and not sent_calls(tmp_path)


def test_legacy_child_can_preview_existing_unlaunched_run(tmp_path, capsys):
    run = create_run(tmp_path, "fixture", run_id="preview", rounds=1, providers="all")
    original = {path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()}
    write_config(tmp_path, contracts={})
    args = ["--project", str(tmp_path), "ask", "preview", "--web-chat", str(FAKE)]
    assert main(args + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert {path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()} == original
    assert main(args + ["--opt-in"]) == 2
    assert "versioned recovery contracts" in capsys.readouterr().err
    assert {path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()} == original
    assert not sent_calls(tmp_path)


@pytest.mark.parametrize("admission", ["admitted", "not_admitted", "unknown"])
def test_duplicate_key_refusal_reconciles_prior_effect_instead_of_new_run(tmp_path, admission):
    run = create_run(tmp_path, "fixture", run_id="duplicate", rounds=1, providers=["chatgpt"])
    child = WebChatChild(FAKE, tmp_path)
    prior = ask(run, child, opt_in=True)
    # Simulate a partial parent restore while the original keyed child remains.
    (run / "rounds/1/launch.json").unlink()
    write_config(tmp_path, admission=admission, refusal={
        "outcome": "refused", "phase": "pre_admission", "effect": "none",
        "code": "duplicate_request_key", "message": "an existing keyed request is retained"})
    if admission == "admitted":
        recovered = ask(run, child)
        assert recovered["state"] == "launched" and recovered["child_run_id"] == prior["child_run_id"]
    else:
        with pytest.raises(ValueError, match="unknown"):
            ask(run, child)
        launch = json.loads((run / "rounds/1/launch.json").read_text())
        assert launch["state"] == "unknown" and "child_run_id" not in launch
        assert progress(run, synthesize(run)[0])["action"] == "collect"
    assert len(list((tmp_path / ".ihav_space/ihav-web-chat/runs").iterdir())) == 1
    before = len(sent_calls(tmp_path))
    if admission == "admitted":
        ask(run, child)
    else:
        with pytest.raises(ValueError, match="unknown"):
            ask(run, child)
    assert len(sent_calls(tmp_path)) == before

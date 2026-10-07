import json
import socket
from pathlib import Path

import pytest

from ihav_competitor_search.chatbots import WebChatChild, ask, collect, prepare, preview, TERMINAL, MAX_ANSWER_BYTES
from ihav_competitor_search.cli import main, synthesize
from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search import chatbots
from ihav_competitor_search.render import export

FAKE = Path(__file__).parent / "fixtures/fake_web_chat.py"


@pytest.fixture(autouse=True)
def sockets_blocked(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def run(project):
    directory = project / ".ihav_space/ihav-competitor-search/runs/chat"
    (directory / "rounds").mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps({"domain": "OCR APIs\nuser text", "options": {"rounds": 2}}))
    return directory


def sends(project):
    path = project / "chat-sends.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def config(project, **values):
    (project / "fake-chat-config.json").write_text(json.dumps(values))


def test_gate_preview_dry_run_consent_and_idempotency(tmp_path, capsys):
    directory = run(tmp_path)
    args = ["--project", str(tmp_path), "ask", "chat", "--web-chat", str(FAKE)]
    child = WebChatChild(FAKE, tmp_path)
    assert child.gate()["version"] == "fake-released-1"
    assert main(args + ["--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "OCR APIs\nuser text" in output["preview"]["prompt"]
    assert output["preview"]["providers"] == ["chatgpt", "gemini", "perplexity"]
    assert not sends(tmp_path) and not (directory / "rounds/1").exists()
    assert "outbound_consent" not in json.loads((directory / "request.json").read_text())
    assert main(args) == 2
    assert not sends(tmp_path)
    assert main(args + ["--opt-in"]) == 0
    assert len(sends(tmp_path)) == 1
    assert sends(tmp_path)[0]["consent"]["domain"] == "OCR APIs\nuser text"
    assert main(["--project", str(tmp_path), "resume", "chat", "--web-chat", str(FAKE)]) == 0
    assert len(sends(tmp_path)) == 1


def test_current_child_missing_delivery_gate_never_launches(tmp_path, capsys):
    directory = run(tmp_path)
    config(tmp_path, missing_delivery=True)
    assert main(["--project", str(tmp_path), "ask", "chat", "--web-chat", str(FAKE), "--opt-in"]) == 2
    error = capsys.readouterr().err
    assert "delivery wait, delivery read" in error
    assert "--web-chat" in error and "IHAV_WEB_CHAT" in error and "doctor --json" in error
    assert "ihav-web-chat@ihav" not in error
    assert not sends(tmp_path) and not (directory / "rounds/1/launch.json").exists()
    config(tmp_path, commands=["run", "status", "delivery wait"])
    with pytest.raises(ValueError, match="delivery read"):
        WebChatChild(FAKE, tmp_path).gate()


def test_committed_launch_lost_response_recovers_exact_identity_without_resend(tmp_path):
    directory = run(tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert len(sends(tmp_path)) == 1
    launch = ask(directory, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert launch["state"] == "launched" and launch["child_run_id"] == sends(tmp_path)[0]["run_id"]
    assert len(sends(tmp_path)) == 1


def test_abrupt_crash_after_effect_preserves_launching_intent(tmp_path, monkeypatch):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    original = child.launch
    def crash(*args):
        original(*args)
        raise KeyboardInterrupt()
    monkeypatch.setattr(child, "launch", crash)
    with pytest.raises(KeyboardInterrupt):
        ask(directory, child, opt_in=True)
    assert json.loads((directory / "rounds/1/launch.json").read_text())["state"] == "launching"
    assert ask(directory, WebChatChild(FAKE, tmp_path))["state"] == "launched"
    assert len(sends(tmp_path)) == 1


@pytest.mark.parametrize("state", sorted(TERMINAL))
def test_terminal_states_and_zero_answer_stop(tmp_path, state):
    directory = run(tmp_path)
    config(tmp_path, statuses={p: {"status": state} for p in ["chatgpt", "gemini", "perplexity"]})
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    record = collect(directory, child)
    assert {v["status"] for v in record["providers"].values()} == {state}
    if state == "completed":
        assert record["state"] == "completed" and "stop_reason" not in record
        assert record["parsed_answers"] == 3
    else:
        assert record["stop_reason"] == "zero_completed_round_1"
        assert record["state"] == ("partial_unresolved" if state == "sent_unknown" else "partial")


def test_partial_unresolved_and_later_projection(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, statuses={"chatgpt": {"status": "completed"}, "gemini": {"status": "sent_unknown"}, "perplexity": {"status": "sent_unknown"}})
    assert collect(directory, child)["state"] == "partial_unresolved"
    table = merge_round(empty_table(), [{"provider": "fake", "raw": {"columns": [], "candidates": [{"name": "Product", "homepage": "https://example.com", "values": {}}]}}], 1)
    table["request"] = {"notes": "DO NOT SEND"}
    (directory / "table.json").write_text(json.dumps(table))
    request = json.loads((directory / "request.json").read_text())
    outbound = preview(request, ["chatgpt", "gemini", "perplexity"], 2, table)
    assert "DO NOT SEND" not in outbound["prompt"] and "only new candidates" in outbound["prompt"]
    with pytest.raises(ValueError, match="previous round"):
        ask(directory, child, 2, table=table)
    assert len(sends(tmp_path)) == 1
    config(tmp_path)
    collect(directory, child)
    fresh, _ = synthesize(directory)
    ask(directory, child, 2, table=fresh)
    assert len(sends(tmp_path)) == 2
    config(tmp_path, statuses={p: {"status": "failed"} for p in ["chatgpt", "gemini", "perplexity"]})
    assert collect(directory, child, 2)["next"] == "verification"


def test_waiting_and_changed_scope_never_relaunch(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, statuses={p: {"status": "queued"} for p in ["chatgpt", "gemini", "perplexity"]})
    assert collect(directory, child)["state"] == "waiting"
    request = json.loads((directory / "request.json").read_text())
    request["domain"] = "changed"
    (directory / "request.json").write_text(json.dumps(request))
    with pytest.raises(ValueError, match="changed"):
        ask(directory, child)
    assert len(sends(tmp_path)) == 1


def test_deadline_preserves_child_status_and_waits_for_unresolved_transport(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    path = directory / "rounds/1/launch.json"
    launch = json.loads(path.read_text())
    launch["deadline"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(launch))
    config(tmp_path, statuses={p: {"status": "running"} for p in ["chatgpt", "gemini", "perplexity"]})
    record = collect(directory, child)
    assert record["deadline_passed"] and record["next"] == "wait" and record["state"] == "waiting"
    assert all(p["status"] == "running" for p in record["providers"].values())
    table = empty_table()
    table["chatbot_rounds"] = {"1": record}
    export(table, directory)
    assert "deadline_passed" in (directory / "report.html").read_text()


def test_provider_all_and_changed_consent_are_bounded(tmp_path):
    directory = run(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    request["providers"] = "all"
    request["outbound_consent"] = {"field_kinds": ["private notes"], "providers": ["chatgpt"], "domain": request["domain"]}
    (directory / "request.json").write_text(json.dumps(request))
    with pytest.raises(ValueError, match="scope changed"):
        ask(directory, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert not sends(tmp_path)
    assert preview(request, ["chatgpt", "gemini"])["providers"] == ["chatgpt", "gemini"]


@pytest.mark.parametrize("bad_status", ["invented", ["completed"], {"status": "completed"}, None])
def test_invalid_status_does_not_invent_completion(tmp_path, bad_status):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, statuses={p: {"status": bad_status} for p in ["chatgpt", "gemini", "perplexity"]})
    with pytest.raises(ValueError, match="invalid provider status"):
        collect(directory, child)
    assert not (directory / "rounds/1/children.json").exists()


def calls(project):
    return [json.loads(line) for line in (project / "chat-calls.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("lookup_ids", [[], ["20261006T000000-00000001", "20261006T000000-00000002"],
                                        ["20260230T000000-00000001"], ["../escape"]])
def test_unknown_launch_lookup_ambiguity_never_resends(tmp_path, lookup_ids):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child, opt_in=True)
    config(tmp_path, lookup_ids=lookup_ids)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child)
    assert len(sends(tmp_path)) == 1
    assert json.loads((directory / "rounds/1/launch.json").read_text())["state"] == "unknown"


@pytest.mark.parametrize("changed", [{"schema_version": True}, {"prompt": "different"},
                                    {"providers": ["chatgpt"]}, {"request_key": "different"},
                                    {"run_id": "20261006T000000-00000002"}, {"response_path": "/outside"}])
def test_lookup_and_collection_revalidate_exact_child_request(tmp_path, changed):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(directory, child, opt_in=True)
    path = child.project / ".ihav_space/ihav-web-chat/runs" / launch["child_run_id"] / "request.json"
    request = json.loads(path.read_text())
    request.update(changed)
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match="unknown"):
        collect(directory, child)
    assert len(sends(tmp_path)) == 1 and not (directory / "rounds/1/answers").exists()


def test_child_request_symlink_is_refused_without_following_it(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(directory, child, opt_in=True)
    path = child.project / ".ihav_space/ihav-web-chat/runs" / launch["child_run_id"] / "request.json"
    outside = tmp_path / "outside-request.json"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="unknown"):
        collect(directory, child)
    assert not (directory / "rounds/1/children.json").exists()


def test_inline_delivery_is_exact_immutable_and_does_not_read_payload_paths(tmp_path, monkeypatch):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(directory, child, opt_in=True)
    raw = '\r\n```json\r\n{"columns": [], "candidates": []}\r\n```\r\n'
    outside = tmp_path / "do-not-read"
    outside.write_text("private")
    config(tmp_path, answers={p: raw for p in launch["providers"]}, response_path=str(outside))
    original = Path.read_text
    def bounded_read(path, *args, **kwargs):
        assert path != outside, "response_path payload must never be read"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", bounded_read)
    record = collect(directory, child)
    assert record["completed_answers"] == record["parsed_answers"] == 3
    path = directory / "rounds/1/answers/chatgpt.json"
    saved = json.loads(path.read_text())
    assert saved["raw"] == raw and saved["provider"] == "chatgpt"
    assert saved["child_run_id"] == launch["child_run_id"] and saved["request_key"] == launch["request_key"]
    assert len(saved["raw_sha256"]) == 64 and saved["fetched_at"]
    before = path.read_bytes()
    assert collect(directory, child)["ingestion"]["chatgpt"]["preserved"]
    assert path.read_bytes() == before
    config(tmp_path, answers={"chatgpt": '{"columns": [], "candidates": []}\nchanged'})
    with pytest.raises(ValueError, match="changed"):
        collect(directory, child)
    assert path.read_bytes() == before


@pytest.mark.parametrize("raw,expected", [(None, "unreadable"), ("", "unreadable"),
                                        ("not JSON", "parse_failed"),
                                        ('{"columns": [], "candidates": [], "value": 1e400}', "parse_failed"),
                                        ("x" * (MAX_ANSWER_BYTES + 1), "unreadable")],
                         ids=["missing", "empty", "malformed", "nonfinite", "oversize"])
def test_completed_delivery_is_not_usable_without_bounded_parsed_text(tmp_path, raw, expected):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(directory, child, opt_in=True)
    config(tmp_path, answers={p: raw for p in launch["providers"]})
    record = collect(directory, child)
    assert record["completed_answers"] == 3 and record["parsed_answers"] == 0
    assert record["state"] == "partial" and record["stop_reason"] == "zero_parsed_round_1"
    assert all(v["status"] == expected for v in record["ingestion"].values())
    if expected == "parse_failed":
        assert json.loads((directory / "rounds/1/answers/chatgpt.json").read_text())["raw"] == raw
    else:
        assert not (directory / "rounds/1/answers").exists()


def test_legacy_terminal_record_without_answers_is_collected_again(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(directory, child, opt_in=True)
    (directory / "rounds/1/children.json").write_text(json.dumps({"state": "completed", "completed_answers": 3}))
    record = collect(directory, child)
    assert record["parsed_answers"] == 3
    assert any(args[:3] == ["delivery", "read", launch["child_run_id"]] and "--include-text" in args for args in calls(tmp_path))


def test_delivery_refusal_keeps_saved_answers_and_explicit_failure(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    collect(directory, child)
    path = directory / "rounds/1/answers/chatgpt.json"
    before = path.read_bytes()
    config(tmp_path, delivery_failure=True)
    record = collect(directory, child)
    assert record["completed_answers"] == record["parsed_answers"] == 3
    assert record["state"] == "partial"
    assert record["ingestion"]["chatgpt"]["reason"] == "delivery_unreadable"
    assert path.read_bytes() == before


@pytest.mark.parametrize("unreadable", ["missing", "blank", "oversize"])
def test_repeat_inline_unreadability_preserves_parsed_answers_with_partial_state(tmp_path, monkeypatch, unreadable):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    assert collect(directory, child)["parsed_answers"] == 3
    paths = list((directory / "rounds/1/answers").glob("*.json"))
    before = {path: path.read_bytes() for path in paths}
    original = child.delivery
    def unreadable_delivery(run_id):
        payload = original(run_id)
        for entry in payload["providers"].values():
            if unreadable == "missing":
                entry.pop("response_text", None)
            else:
                entry["response_text"] = "  \n" if unreadable == "blank" else "x" * (MAX_ANSWER_BYTES + 1)
        return payload
    monkeypatch.setattr(child, "delivery", unreadable_delivery)
    record = collect(directory, child)
    assert record["parsed_answers"] == record["completed_answers"] == 3
    assert record["state"] == "partial" and "stop_reason" not in record
    reason = "answer_exceeds_1_mib" if unreadable == "oversize" else "completed_response_missing_or_empty"
    assert all(v["status"] == "unreadable" and v["reason"] == reason and v["parsed"] and v["preserved"]
               and len(v["raw_sha256"]) == 64 for v in record["ingestion"].values())
    assert all(path.read_bytes() == content for path, content in before.items())


def test_status_run_mismatch_cannot_be_counted(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, status_run_id="20261006T000000-000000ff")
    with pytest.raises(ValueError, match="identify the run"):
        collect(directory, child)
    assert not (directory / "rounds/1/children.json").exists()


def test_lookup_refusal_keeps_unknown_intent_and_does_not_relaunch(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child, opt_in=True)
    config(tmp_path, lookup_failure=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child)
    assert len(sends(tmp_path)) == 1
    assert json.loads((directory / "rounds/1/launch.json").read_text())["state"] == "unknown"


def test_sent_unknown_never_reads_answer_or_starts_another_round(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, statuses={"chatgpt": {"status": "completed"}, "gemini": {"status": "sent_unknown"},
                              "perplexity": {"status": "failed"}})
    record = collect(directory, child)
    assert record["state"] == "partial_unresolved" and record["parsed_answers"] == 1
    assert not (directory / "rounds/1/answers/gemini.json").exists()
    with pytest.raises(ValueError, match="previous round"):
        ask(directory, child, 2, table=empty_table())
    assert len(sends(tmp_path)) == 1


def test_later_preview_requires_supplied_fresh_table_and_saved_launch_keeps_prompt(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    config(tmp_path, answers={p: manual_raw("Fresh product", "https://example.com")
                             for p in ["chatgpt", "gemini", "perplexity"]})
    ask(directory, child, opt_in=True)
    collect(directory, child)
    stale = empty_table()
    (directory / "table.json").write_text(json.dumps(stale))
    fresh, _ = synthesize(directory)
    with pytest.raises(ValueError, match="saved synthesis"):
        prepare(directory, child, 2)
    assert "Fresh product" in prepare(directory, child, 2, table=fresh)[2]["prompt"]
    launch = ask(directory, child, 2, table=fresh)
    assert "Fresh product" in sends(tmp_path)[1]["prompt"]
    assert prepare(directory, child, 2, table=empty_table())[2]["prompt"] == sends(tmp_path)[1]["prompt"]
    assert prepare(directory, child, 2)[2]["prompt"] == sends(tmp_path)[1]["prompt"]
    assert ask(directory, child, 2, table=empty_table()) == launch
    assert len(sends(tmp_path)) == 2


@pytest.mark.parametrize("unknown", [False, True], ids=["launched", "unknown"])
def test_resume_dry_run_previews_exact_saved_prompt_after_table_changes(tmp_path, capsys, unknown):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    config(tmp_path, answers={p: manual_raw("Saved product", "https://saved.example")
                             for p in ["chatgpt", "gemini", "perplexity"]})
    ask(directory, child, opt_in=True)
    collect(directory, child)
    initial, _ = synthesize(directory)
    ask(directory, child, 2, table=initial)
    expected = sends(tmp_path)[1]["prompt"]
    if unknown:
        launch_path = directory / "rounds/2/launch.json"
        launch = json.loads(launch_path.read_text())
        launch.pop("child_run_id")
        launch["state"] = "unknown"
        launch_path.write_text(json.dumps(launch))
    (directory / "table.json").write_text(json.dumps(empty_table()))
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    before_calls = len(calls(tmp_path))
    assert main(["--project", str(tmp_path), "resume", "chat", "--round", "2",
                 "--web-chat", str(FAKE), "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] and output["preview"]["prompt"] == expected
    assert "Saved product" in output["preview"]["prompt"]
    assert output["preview"]["providers"] == ["chatgpt", "gemini", "perplexity"]
    assert output["preview"]["outbound_field_kinds"] == ["domain verbatim", "candidate name", "candidate homepage", "accepted column values"]
    assert output["preview"]["domain_sent_verbatim"]
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
    assert calls(tmp_path)[before_calls:] == [["--help"], ["doctor", "--json"]]
    assert len(sends(tmp_path)) == 2


def test_saved_prompt_and_child_identity_preserve_domain_crlf(tmp_path):
    directory = run(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    request["domain"] = "OCR APIs\r\nverbatim"
    (directory / "request.json").write_text(json.dumps(request))
    child = WebChatChild(FAKE, tmp_path)
    launch = ask(directory, child, opt_in=True)
    assert request["domain"] in sends(tmp_path)[0]["prompt"]
    assert ask(directory, child) == launch
    assert collect(directory, child)["parsed_answers"] == 3
    assert len(sends(tmp_path)) == 1


def test_answer_symlink_is_refused_before_child_discovery(tmp_path):
    directory = run(tmp_path)
    answers = directory / "rounds/1/answers"
    answers.mkdir(parents=True)
    outside = tmp_path / "outside-answer.json"
    outside.write_text(json.dumps({"method": "manual_paste"}))
    (answers / "chatgpt.json").symlink_to(outside)
    with pytest.raises(ValueError, match="answer file escapes"):
        ask(directory, object(), opt_in=True)


def manual_raw(name="Manual product", homepage="https://manual.example"):
    return json.dumps({"columns": [], "candidates": [{"name": name, "homepage": homepage, "values": {}}]})


def test_manual_first_round_can_switch_to_child_second_round_with_fresh_preview(tmp_path, capsys):
    directory = run(tmp_path)
    import_answer(directory, 1, "ManualClaude", manual_raw())
    (directory / "table.json").write_text("stale output ignored")
    assert main(["--project", str(tmp_path), "ask", "chat", "--round", "2", "--web-chat", str(FAKE), "--opt-in"]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "Manual product" in output[0]["preview"]["prompt"]
    assert "https://manual.example" in output[0]["preview"]["prompt"]
    assert "manual_paste" not in output[0]["preview"]["prompt"]
    assert output[1]["state"] == "launched" and len(sends(tmp_path)) == 1
    assert not (directory / "rounds/1/children.json").exists()
    assert not (directory / "rounds/1/launch.json").exists()


@pytest.mark.parametrize("invalid", ["parse_failed", "empty", "missing_status", "forged_status", "wrong_provider", "missing_answers"])
def test_unusable_previous_manual_round_refuses_child_launch(tmp_path, capsys, invalid):
    directory = run(tmp_path)
    if invalid != "missing_answers":
        raw = "not JSON" if invalid in {"parse_failed", "forged_status"} else '{"columns": [], "candidates": []}' if invalid == "empty" else manual_raw()
        import_answer(directory, 1, "manual", raw)
        path = directory / "rounds/1/answers/manual.json"
        record = json.loads(path.read_text())
        if invalid == "missing_status":
            record.pop("status")
        elif invalid == "forged_status":
            record["status"] = "completed"
        elif invalid == "wrong_provider":
            record["provider"] = "different"
        path.write_text(json.dumps(record))
    before = (directory / "request.json").read_bytes()
    assert main(["--project", str(tmp_path), "ask", "chat", "--round", "2", "--web-chat", str(FAKE), "--opt-in"]) == 2
    expected = {"forged_status": "saved answer status", "wrong_provider": "saved answer identity"}
    assert expected.get(invalid, "previous") in capsys.readouterr().err
    assert not sends(tmp_path) and not (directory / "rounds/2/launch.json").exists()
    assert (directory / "request.json").read_bytes() == before


def test_manual_repeat_stop_is_recomputed_using_all_prior_rounds(tmp_path, capsys):
    directory = run(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    request["options"]["rounds"] = 3
    (directory / "request.json").write_text(json.dumps(request))
    import_answer(directory, 1, "manual", manual_raw())
    import_answer(directory, 2, "manual", manual_raw())
    assert main(["--project", str(tmp_path), "ask", "chat", "--round", "3", "--web-chat", str(FAKE), "--opt-in"]) == 2
    assert "stopped the loop" in capsys.readouterr().err
    assert not sends(tmp_path) and not (directory / "rounds/3/launch.json").exists()


def test_manual_later_round_uses_all_prior_column_budgets_before_child_launch(tmp_path, capsys):
    directory = run(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    request["options"] = {"rounds": 3, "max_new_columns": 1, "max_total_columns": 12}
    (directory / "request.json").write_text(json.dumps(request))
    first = json.loads(manual_raw())
    first["columns"] = [{"key": "custom", "meaning": "custom feature", "type": "text"}]
    import_answer(directory, 1, "manual", json.dumps(first))
    import_answer(directory, 2, "manual", manual_raw("Second manual product", "https://second.example"))
    assert main(["--project", str(tmp_path), "ask", "chat", "--round", "3", "--web-chat", str(FAKE), "--opt-in"]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "Manual product" in output[0]["preview"]["prompt"] and "Second manual product" in output[0]["preview"]["prompt"]
    assert "custom" in output[0]["preview"]["prompt"]
    assert len(sends(tmp_path)) == 1


def test_forged_manual_progress_cannot_override_saved_stop(tmp_path):
    directory = run(tmp_path)
    import_answer(directory, 1, "manual", '{"columns": [], "candidates": []}')
    forged = merge_round(empty_table(), [{"provider": "manual", "method": "manual_paste", "raw": manual_raw()}], 1)
    with pytest.raises(ValueError, match="stopped the loop"):
        ask(directory, WebChatChild(FAKE, tmp_path), 2, opt_in=True, table=forged)
    assert not sends(tmp_path)


def test_previous_uncollected_child_cannot_be_relabelled_as_manual(tmp_path):
    directory = run(tmp_path)
    import_answer(directory, 1, "manual", manual_raw())
    (directory / "rounds/1/launch.json").write_text('{}')
    table = merge_round(empty_table(), [{"provider": "manual", "method": "manual_paste", "raw": manual_raw()}], 1)
    with pytest.raises(ValueError, match="requires collection"):
        ask(directory, WebChatChild(FAKE, tmp_path), 2, opt_in=True, table=table)
    assert not sends(tmp_path)


def test_large_manual_round_gap_checks_only_recorded_folders(tmp_path, monkeypatch, capsys):
    directory = run(tmp_path)
    previous_number = 1_000_000
    request = json.loads((directory / "request.json").read_text())
    request["options"]["rounds"] = previous_number + 1
    (directory / "request.json").write_text(json.dumps(request))
    visited = []
    original = chatbots.round_folder
    def bounded_folder(run_directory, number):
        assert number in {previous_number - 1, previous_number, previous_number + 1}, "must not scan absent numeric rounds"
        visited.append(number)
        return original(run_directory, number)
    monkeypatch.setattr(chatbots, "round_folder", bounded_folder)
    with pytest.raises(ValueError, match="previous round"):
        import_answer(directory, previous_number, "manual", manual_raw())
    assert not (directory / "rounds" / str(previous_number)).exists()
    # A legacy orphan may already exist; synthesis must also reject its gap.
    answers = directory / "rounds" / str(previous_number) / "answers"
    answers.mkdir(parents=True)
    (answers / "legacy.json").write_text(json.dumps({"provider": "legacy", "raw": manual_raw()}))
    assert main(["--project", str(tmp_path), "ask", "chat", "--round", str(previous_number + 1),
                 "--web-chat", str(FAKE), "--opt-in"]) == 2
    assert "previous round" in capsys.readouterr().err
    assert len(visited) < 10 and not sends(tmp_path)


@pytest.mark.parametrize("issue", ["sent_unknown", "waiting", "unknown_launch", "uncollected", "stopped", "zero_usable"])
def test_manual_middle_round_cannot_bypass_earlier_unresolved_child_work(tmp_path, issue):
    directory = run(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    request["options"]["rounds"] = 3
    (directory / "request.json").write_text(json.dumps(request))
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    if issue in {"sent_unknown", "waiting"}:
        config(tmp_path, statuses={"chatgpt": {"status": "completed"},
                                  "gemini": {"status": "sent_unknown" if issue == "sent_unknown" else "running"},
                                  "perplexity": {"status": "failed"}})
    elif issue == "zero_usable":
        config(tmp_path, answers={p: "not JSON" for p in ["chatgpt", "gemini", "perplexity"]})
    if issue != "uncollected":
        first = collect(directory, child)
        if issue == "sent_unknown":
            assert first["state"] == "partial_unresolved" and first["parsed_answers"] == 1
        elif issue == "waiting":
            assert first["state"] == "waiting" and first["parsed_answers"] == 1
    if issue == "unknown_launch":
        path = directory / "rounds/1/launch.json"
        launch = json.loads(path.read_text())
        launch["state"] = "unknown"
        path.write_text(json.dumps(launch))
    elif issue == "stopped":
        path = directory / "rounds/1/children.json"
        record = json.loads(path.read_text())
        record["stop_reason"] = "zero_completed_round_1"
        path.write_text(json.dumps(record))
    before_import = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    before_import_calls = len(calls(tmp_path))
    with pytest.raises(ValueError, match="previous round"):
        import_answer(directory, 2, "manual", manual_raw("Middle manual product", "https://middle.example"))
    assert len(calls(tmp_path)) == before_import_calls
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before_import
    # Preserve coverage for bad dependency state saved by older implementations.
    answers = directory / "rounds/2/answers"
    answers.mkdir(parents=True)
    (answers / "manual.json").write_text(json.dumps({
        "provider": "manual", "method": "manual_paste", "status": "completed",
        "fetched_at": "2026-10-06T00:00:00Z", "raw": manual_raw("Middle manual product", "https://middle.example")
    }))
    if issue in {"unknown_launch", "uncollected"}:
        expected = "saved answer child provenance" if issue == "unknown_launch" else "previous round"
        with pytest.raises(ValueError, match=expected):
            synthesize(directory, through_round=2)
        assert len(sends(tmp_path)) == 1 and not (directory / "rounds/3/launch.json").exists()
        return
    table, _ = synthesize(directory, through_round=2)
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    before_calls = len(calls(tmp_path))
    with pytest.raises(ValueError, match="previous round"):
        ask(directory, child, 3, table=table)
    assert len(sends(tmp_path)) == 1 and not (directory / "rounds/3/launch.json").exists()
    assert calls(tmp_path)[before_calls:] == [["--help"], ["doctor", "--json"]]
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("partial", [False, True], ids=["completed", "partial_usable"])
def test_completed_child_then_manual_can_switch_back_to_child(tmp_path, partial):
    directory = run(tmp_path)
    request = json.loads((directory / "request.json").read_text())
    request["options"]["rounds"] = 3
    (directory / "request.json").write_text(json.dumps(request))
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    if partial:
        config(tmp_path, statuses={"chatgpt": {"status": "completed"}, "gemini": {"status": "failed"},
                                  "perplexity": {"status": "not_sent"}})
    first = collect(directory, child)
    assert first["state"] == ("partial" if partial else "completed")
    import_answer(directory, 2, "manual", manual_raw("Middle manual product", "https://middle.example"))
    table, _ = synthesize(directory, through_round=2)
    before_calls = len(calls(tmp_path))
    launch = ask(directory, child, 3, table=table)
    assert launch["state"] == "launched" and len(sends(tmp_path)) == 2
    assert "Fixture product" in sends(tmp_path)[1]["prompt"]
    assert "Middle manual product" in sends(tmp_path)[1]["prompt"]
    subsequent = calls(tmp_path)[before_calls:]
    assert subsequent[:2] == [["--help"], ["doctor", "--json"]]
    assert len(subsequent) == 3 and subsequent[-1][0] == "run" and subsequent[-1][1] != "lookup"

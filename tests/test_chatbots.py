import json
import socket
from pathlib import Path

import pytest

from ihav_competitor_search.chatbots import WebChatChild, ask, collect, preview, TERMINAL
from ihav_competitor_search.cli import main
from ihav_competitor_search.merge import empty_table, merge_round
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
    assert child.gate()["version"] == "fake-proposed-1"
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
    assert "delivery wait, delivery read" in error and "claude plugin install ihav-web-chat@ihav" in error
    assert not sends(tmp_path) and not (directory / "rounds/1/launch.json").exists()
    config(tmp_path, commands=["run", "status", "delivery wait"])
    with pytest.raises(ValueError, match="delivery read"):
        WebChatChild(FAKE, tmp_path).gate()


def test_committed_launch_lost_response_stops_resume(tmp_path):
    directory = run(tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert len(sends(tmp_path)) == 1
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, WebChatChild(FAKE, tmp_path), opt_in=True)
    assert len(sends(tmp_path)) == 1
    assert json.loads((directory / "rounds/1/launch.json").read_text())["state"] == "unknown"


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
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, WebChatChild(FAKE, tmp_path))
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
    ask(directory, child, 2)
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


def test_deadline_preserves_child_status_and_stops_zero_answers(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    path = directory / "rounds/1/launch.json"
    launch = json.loads(path.read_text())
    launch["deadline"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(launch))
    config(tmp_path, statuses={p: {"status": "running"} for p in ["chatgpt", "gemini", "perplexity"]})
    record = collect(directory, child)
    assert record["deadline_passed"] and record["next"] == "stop"
    assert all(p["status"] == "running" for p in record["providers"].values())
    table = empty_table()
    table["chatbot_rounds"] = {"1": record}
    export(table, directory)
    assert "zero_completed_round_1" in (directory / "report.html").read_text()


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


def test_invalid_status_does_not_invent_completion(tmp_path):
    directory = run(tmp_path)
    child = WebChatChild(FAKE, tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, statuses={p: {"status": "invented"} for p in ["chatgpt", "gemini", "perplexity"]})
    with pytest.raises(ValueError, match="invalid provider status"):
        collect(directory, child)
    assert not (directory / "rounds/1/children.json").exists()

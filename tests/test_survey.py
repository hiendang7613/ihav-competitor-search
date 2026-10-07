"""Survey uses bounded saved-state steps; tests never start a real child."""
import json
from pathlib import Path

import pytest

from ihav_competitor_search import cli
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search.survey import create_run, progress


def answer(name="A"):
    return json.dumps({"columns": [], "candidates": [{"name": name, "homepage": "https://example.com", "values": {}}]})


def test_init_validates_before_create_and_preserves_existing(tmp_path, capsys):
    args = ["--project", str(tmp_path), "init", "domain verbatim\n", "--run-id", "survey"]
    assert cli.main(args + ["--rounds", "0"]) == 2
    assert not (tmp_path / ".ihav_space").exists()
    assert cli.main(args) == 0
    directory = tmp_path / ".ihav_space/ihav-competitor-search/runs/survey"
    request = (directory / "request.json").read_bytes()
    assert json.loads(request)["domain"] == "domain verbatim\n"
    assert cli.main(args) == 2
    assert (directory / "request.json").read_bytes() == request
    capsys.readouterr()


def test_run_root_symlink_refused_without_writing(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".ihav_space").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        create_run(tmp_path, "domain")
    assert not list(outside.iterdir())


def test_saved_progress_requires_real_homepage_decision_and_traffic(tmp_path):
    directory = create_run(tmp_path, "domain", rounds=1)
    assert progress(directory, cli.synthesize(directory)[0])["state"] == "ready_to_queue"
    import_answer(directory, 1, "manual", answer())
    table = cli.synthesize(directory)[0]
    assert progress(directory, table)["state"] == "homepage_confirmation_pending"
    candidate = table["rows"][0]["candidate_id"]
    from ihav_competitor_search.homepages import confirm_homepage
    confirm_homepage(table, directory, candidate, "https://example.com", method="official_search")
    assert progress(directory, cli.synthesize(directory)[0])["state"] == "traffic_lookup_pending"
    (directory / "visits.json").write_text(json.dumps({"example.com": {"exit_code": 2}}))
    assert progress(directory, cli.synthesize(directory)[0])["state"] == "verification_pending"


def test_partial_render_does_not_merge_empty_queued_round(tmp_path, capsys):
    directory = create_run(tmp_path, "domain", run_id="queued")
    folder = directory / "rounds/1"
    folder.mkdir(parents=True)
    (folder / "launch.json").write_text(json.dumps({"child_run_id": "saved-child", "state": "launched"}))
    table = cli.synthesize(directory)[0]
    assert table["rounds"] == []
    assert progress(directory, table)["state"] == "awaiting_child"
    assert cli.main(["--project", str(tmp_path), "render", "queued"]) == 0
    assert json.loads(capsys.readouterr().out)["survey"]["state"] == "awaiting_child"


def test_previously_skipped_host_is_pending_after_confirmation(tmp_path):
    from ihav_competitor_search.homepages import confirm_homepage
    directory = create_run(tmp_path, "domain", rounds=1, max_lookups=1)
    import_answer(directory, 1, "manual", answer())
    table = cli.synthesize(directory)[0]
    confirm_homepage(table, directory, table["rows"][0]["candidate_id"], "https://example.com", method="official_search")
    (directory / "visits.json").write_text(json.dumps({"example.com": {"exit_code": None, "state": "skipped", "reason": "homepage_unconfirmed"}}))
    assert progress(directory, cli.synthesize(directory)[0])["state"] == "traffic_lookup_pending"


def test_survey_preview_no_consent_creates_no_run(tmp_path, capsys):
    fake = Path(__file__).parent / "fixtures/fake_web_chat.py"
    args = ["--project", str(tmp_path), "survey", "domain", "--web-chat", str(fake)]
    assert cli.main(args + ["--rounds", "0", "--dry-run"]) == 2
    assert not (tmp_path / ".ihav_space").exists()
    capsys.readouterr()
    assert cli.main(args + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "consent_required"
    assert not (tmp_path / ".ihav_space").exists()


def test_survey_queues_then_collects_without_second_queue(tmp_path, capsys):
    fake = Path(__file__).parent / "fixtures/fake_web_chat.py"
    start = ["--project", str(tmp_path), "survey", "domain", "--run-id", "auto", "--web-chat", str(fake), "--opt-in"]
    assert cli.main(start) == 0
    capsys.readouterr()
    resume = ["--project", str(tmp_path), "survey", "--run-id", "auto", "--web-chat", str(fake)]
    assert cli.main(resume + ["--dry-run"]) == 0
    capsys.readouterr()
    assert cli.main(resume) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "homepage_confirmation_pending"
    assert len((tmp_path / "chat-sends.jsonl").read_text().splitlines()) == 1


def test_lookup_honors_saved_zero_cap_and_explicit_raise(tmp_path, capsys):
    from ihav_competitor_search.homepages import confirm_homepage
    directory = create_run(tmp_path, "domain", run_id="capped", max_lookups=0)
    import_answer(directory, 1, "manual", answer())
    table = cli.synthesize(directory)[0]
    confirm_homepage(table, directory, table["rows"][0]["candidate_id"], "https://example.com", method="official_search")
    fake = Path(__file__).parent / "fixtures/fake_counter.py"
    args = ["--project", str(tmp_path), "lookup", "capped", "--counter", str(fake)]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["attempted"] == 0
    assert cli.main(args + ["--max-lookups", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["attempted"] == 1
    assert json.loads((directory / "request.json").read_text())["options"]["max_lookups"] == 1


@pytest.mark.parametrize("state", ["waiting", "partial", "partial_unresolved", "completed"])
def test_completed_claims_without_parsed_answers_never_advance(tmp_path, state):
    directory = create_run(tmp_path, "domain")
    folder = directory / "rounds/1"
    folder.mkdir(parents=True)
    (folder / "launch.json").write_text(json.dumps({"child_run_id": "saved-child"}))
    (folder / "children.json").write_text(json.dumps({"state": state, "completed_answers": 1, "parsed_answers": 0}))
    expected = "awaiting_child" if state == "waiting" else "unresolved_send" if state == "partial_unresolved" else "zero_usable_answers"
    assert progress(directory, cli.synthesize(directory)[0])["state"] == expected


def test_no_verify_reports_explicit_unverified_completion(tmp_path):
    directory = create_run(tmp_path, "domain", rounds=1, no_verify=True, max_lookups=0)
    import_answer(directory, 1, "manual", answer())
    request = json.loads((directory / "request.json").read_text())
    table = cli.synthesize(directory)[0]
    request["homepage_decisions"] = {table["rows"][0]["candidate_id"]: {"status": "unconfirmed", "reason": "not established"}}
    (directory / "request.json").write_text(json.dumps(request))
    assert progress(directory, cli.synthesize(directory)[0])["state"] == "completed_unverified"


def test_cli_verification_corrects_value_and_renders_source(tmp_path, capsys):
    from ihav_competitor_search.homepages import confirm_homepage
    directory = create_run(tmp_path, "domain", run_id="checked", rounds=1, max_lookups=0)
    import_answer(directory, 1, "manual", answer())
    table = cli.synthesize(directory)[0]
    candidate = table["rows"][0]["candidate_id"]
    confirm_homepage(table, directory, candidate, "https://example.com", method="official_search")
    evidence = tmp_path / "checked.json"
    evidence.write_text(json.dumps({"schema_version": 1, "candidate_id": candidate, "column": "name",
        "value": "A", "type": "text", "source_url": "https://example.com/about", "fetched_at": "2026-10-05T12:00:00Z",
        "method": "page", "raw_excerpt": "A is the product", "checked_by": "host"}))
    assert cli.main(["--project", str(tmp_path), "verify", "checked", "--file", str(evidence)]) == 0
    capsys.readouterr()
    assert cli.main(["--project", str(tmp_path), "render", "checked"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["survey"]["state"] == "completed_with_recorded_evidence"
    assert "https://example.com/about" in (directory / "report.html").read_text()

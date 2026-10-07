"""Synthetic offline research consumers: transport/parse success is not mode proof."""
import copy
import hashlib
import json
import socket
from pathlib import Path

import pytest

from ihav_competitor_search import research
from ihav_competitor_search.chatbots import WebChatChild, ask, collect, prepare, read, save
from ihav_competitor_search.cli import main
from ihav_competitor_search.manual import import_answer, prompt
from ihav_competitor_search.render import export
from ihav_competitor_search.survey import create_run, make_request, progress
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
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic-parent-session")


def research_run(project, *, rounds=2):
    directory = create_run(project, "Synthetic OCR APIs\r\n原文", run_id="typed", targets=copy.deepcopy(TARGETS),
                           rounds=rounds, max_lookups=0, no_verify=True)
    return directory, WebChatChild(FAKE, project)


def config(project, **values):
    save(project / "synthetic-chat-config.json", values)


def queues(project):
    path = project / "synthetic-queues.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def calls(project):
    return [json.loads(line) for line in (project / "synthetic-calls.jsonl").read_text().splitlines()]


def answer_file(directory, provider=PROVIDERS[0], number=1):
    return directory / "rounds" / str(number) / "answers" / (provider + ".json")


def confirm_for_progress(directory, table):
    request = read(directory / "request.json")
    request["homepage_decisions"] = {row["candidate_id"]: {"status": "unconfirmed", "reason": "synthetic fixture"}
                                    for row in table["rows"]}
    save(directory / "request.json", request)


def test_preview_then_explicit_ingress_consent_and_exact_authorization(tmp_path):
    directory, child = research_run(tmp_path)
    _, _, outbound = prepare(directory, child, 1)
    assert outbound["targets"] == TARGETS
    assert outbound["round_budget"]["rounds"] == 2
    assert outbound["authorization_request"]["outbound_scope_sha256"]
    assert not queues(tmp_path) and "outbound_consent" not in read(directory / "request.json")
    with pytest.raises(ValueError, match="opt-in"):
        ask(directory, child)
    launch = ask(directory, child, opt_in=True)
    request = read(directory / "request.json")
    assert request["outbound_consent"]["issuer"] == {"host": "codex", "session": "synthetic-parent-session"}
    assert request["outbound_consent"]["targets"] == TARGETS
    assert launch["request_schema_version"] == 2
    assert launch["request_key"] == outbound["authorization_request"]["request_key"]
    assert read(directory / "rounds/1/authorization.json") == launch["authorization"]
    assert child.request(launch["child_run_id"])["authorization"] == launch["authorization"]
    assert len(queues(tmp_path)) == 1
    assert not any(args[0] == "worker" for args in calls(tmp_path))


def test_unknown_committed_launch_exact_typed_lookup_never_replays(tmp_path, monkeypatch):
    directory, child = research_run(tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child, opt_in=True)
    before = read(directory / "rounds/1/launch.json")
    monkeypatch.setenv("CODEX_THREAD_ID", "different-host-session")
    config(tmp_path, catalog={p: [] for p in PROVIDERS},
           contracts={"run_lookup_admission": 1, "launch_outcome": 1})
    recovered = ask(directory, child)
    assert recovered["child_run_id"] == queues(tmp_path)[0]["run_id"]
    assert recovered["authorization"] == before["authorization"]
    assert recovered["request_key"] == before["request_key"] and len(queues(tmp_path)) == 1
    assert any(args[:2] == ["run", "lookup"] for args in calls(tmp_path))
    assert collect(directory, child)["research"]["requirements_met"]


@pytest.mark.parametrize("changed", [
    {"request_schema_version": 1}, {"request_schema_version": True},
    {"outbound_scope_sha256": "0" * 64}, {"authorization_sha256": "0" * 64},
], ids=["legacy-proof", "bool-version", "scope", "authorization"])
def test_unknown_launch_rejects_incomplete_or_mismatched_typed_admission(tmp_path, changed):
    directory, child = research_run(tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child, opt_in=True)
    config(tmp_path, admission_overrides=changed)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child)
    assert len(queues(tmp_path)) == 1 and read(directory / "rounds/1/launch.json")["state"] == "unknown"


def test_unknown_lookup_changed_scalar_type_cannot_admit(tmp_path):
    directory, child = research_run(tmp_path)
    config(tmp_path, lost_response=True)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child, opt_in=True)
    changed = copy.deepcopy(TARGETS[0])
    changed["settings"]["strict"] = 1
    config(tmp_path, admission_target_override=changed)
    with pytest.raises(ValueError, match="unknown"):
        ask(directory, child)
    assert len(queues(tmp_path)) == 1


@pytest.mark.parametrize("changes", ["missing", "settings", "preparation_only", "wrong_raw", "wrong_job", "wrong_provider",
                                    "wrong_version", "wrong_schema_keys", "wrong_prompt", "wrong_outbound", "no_turn"])
def test_completed_parseable_raw_does_not_qualify_missing_or_mismatched_receipt(tmp_path, changes):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    changed = copy.deepcopy(TARGETS[0])
    changed["settings"]["strict"] = 1  # Python == would conceal this mismatch.
    overrides = {"settings": {"observed": changed}, "preparation_only": {"qualification": "unqualified", "reason": "final_target_observation_missing"},
                 "wrong_raw": {"raw_answer_sha256": "0" * 64}, "wrong_job": {"job_id": "wrong/job"},
                 "wrong_provider": {"provider": PROVIDERS[1]}, "wrong_version": {"schema_version": True},
                 "wrong_schema_keys": {"unexpected": True}, "wrong_prompt": {"prompt_sha256": "0" * 64},
                 "wrong_outbound": {"outbound_sha256": "0" * 64}, "no_turn": {"turn_binding": None}}
    config(tmp_path, missing_receipts=[PROVIDERS[0]] if changes == "missing" else [],
           receipt_overrides={PROVIDERS[0]: overrides.get(changes, {})})
    record = collect(directory, child)
    assert record["state"] == "completed" and record["parsed_answers"] == 2  # transport/parser facts
    assert not record["research"]["requirements_met"]
    assert read(answer_file(directory))["research_qualification"]["state"] == "unverified"
    table, _ = synthesize(directory)
    assert progress(directory, table)["state"] == "research_mode_unverified"
    with pytest.raises(ValueError, match="previous round"):
        ask(directory, child, 2, table=table)
    assert len(queues(tmp_path)) == 1


def test_same_raw_new_turn_cannot_rewrite_saved_attribution(tmp_path):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    collect(directory, child)
    path = answer_file(directory)
    before = path.read_bytes()
    initial = read(path)
    assert "\r\n" in initial["raw"]
    assert initial["raw_sha256"] == hashlib.sha256(initial["raw"].encode("utf-8")).hexdigest()
    binding = dict(initial["execution_receipt"]["turn_binding"], turn=2)
    config(tmp_path, receipt_overrides={PROVIDERS[0]: {"turn_binding": binding}})
    with pytest.raises(ValueError, match="execution receipt changed"):
        collect(directory, child)
    assert path.read_bytes() == before and len(queues(tmp_path)) == 1
    table, _ = synthesize(directory)
    assert progress(directory, table)["state"] == "research_attribution_unresolved"
    assert not table["research"]["requirements_met"]
    with pytest.raises(ValueError, match="previous round"):
        ask(directory, child, 2, table=table)


def test_missing_receipt_cannot_be_silently_upgraded_on_recollect(tmp_path):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    config(tmp_path, missing_receipts=[PROVIDERS[0]])
    collect(directory, child)
    before = answer_file(directory).read_bytes()
    config(tmp_path)
    with pytest.raises(ValueError, match="receipt changed"):
        collect(directory, child)
    assert answer_file(directory).read_bytes() == before


@pytest.mark.parametrize("kind", ["status_subset", "delivery_subset", "failed", "sent_unknown"])
def test_provider_subset_failure_and_unknown_never_qualify_or_advance(tmp_path, kind):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    if kind == "status_subset":
        config(tmp_path, statuses={PROVIDERS[0]: "completed"})
        with pytest.raises(ValueError, match="every selected provider"):
            collect(directory, child)
        assert not (directory / "rounds/1/children.json").exists()
        return
    if kind == "delivery_subset":
        config(tmp_path, delivery_drop=[PROVIDERS[1]])
    else:
        config(tmp_path, statuses={PROVIDERS[0]: "completed", PROVIDERS[1]: kind})
    record = collect(directory, child)
    assert not record["research"]["requirements_met"]
    table, _ = synthesize(directory)
    assert progress(directory, table)["action"] in {"stop", "collect"}
    with pytest.raises(ValueError, match="previous round"):
        ask(directory, child, 2, table=table)
    assert len(queues(tmp_path)) == 1


@pytest.mark.parametrize("changed", ["settings", "budget", "consent_settings", "consent_budget", "issuer", "budget_both", "settings_both"])
def test_persisted_scope_tamper_refuses_known_launch_or_next_round(tmp_path, changed):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    request = read(directory / "request.json")
    if changed == "settings":
        request["targets"][0]["settings"]["strict"] = 1
    elif changed == "budget":
        request["options"]["rounds"] = 3
    elif changed == "consent_settings":
        request["outbound_consent"]["targets"][0]["settings"]["strict"] = 1
    elif changed == "consent_budget":
        request["outbound_consent"]["budgets"]["max_new"] += 1
    elif changed == "issuer":
        request["outbound_consent"]["issuer"]["session"] = "tampered"
    elif changed == "budget_both":
        request["options"]["rounds"] = 3
        request["outbound_consent"]["budgets"]["rounds"] = 3
    else:
        request["targets"][0]["settings"]["strict"] = 1
        request["outbound_consent"]["targets"][0]["settings"]["strict"] = 1
    save(directory / "request.json", request)
    with pytest.raises(ValueError, match="changed"):
        ask(directory, child)
    assert len(queues(tmp_path)) == 1


@pytest.mark.parametrize("filename", ["targets.json", "authorization.json", "prompt.md"])
def test_outbound_file_tamper_never_requeues(tmp_path, filename):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    path = directory / "rounds/1" / filename
    if filename == "prompt.md":
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        value = read(path)
        if filename == "targets.json":
            value["targets"][0]["settings"]["strict"] = 1
        else:
            value["outbound_scope_sha256"] = "0" * 64
        save(path, value)
    with pytest.raises(ValueError, match="changed"):
        ask(directory, child)
    assert len(queues(tmp_path)) == 1


def test_new_research_requires_active_trusted_ingress_but_saved_collection_does_not(tmp_path, monkeypatch):
    directory, child = research_run(tmp_path)
    monkeypatch.delenv("CODEX_THREAD_ID")
    with pytest.raises(ValueError, match="trusted host"):
        ask(directory, child, opt_in=True)
    assert not queues(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic-parent-session")
    ask(directory, child, opt_in=True)
    monkeypatch.setenv("CODEX_THREAD_ID", "other-session")
    assert ask(directory, child)["state"] == "launched"
    collect(directory, child)
    table, _ = synthesize(directory)
    with pytest.raises(ValueError, match="issuer changed"):
        ask(directory, child, 2, table=table)
    assert len(queues(tmp_path)) == 1


def test_two_provider_two_round_synthetic_evidence_and_bounded_finish(tmp_path):
    directory, child = research_run(tmp_path)
    first = ask(directory, child, opt_in=True)
    assert collect(directory, child)["research"]["qualified_providers"] == PROVIDERS
    table, _ = synthesize(directory)
    confirm_for_progress(directory, table)
    table, _ = synthesize(directory)
    assert progress(directory, table)["action"] == "ask"
    second = ask(directory, child, 2, table=table)
    assert first["outbound_scope_sha256"] != second["outbound_scope_sha256"]
    assert second["authorization"]["issuer"] == first["authorization"]["issuer"]
    assert "Synthetic synthetic_alpha round 1" in queues(tmp_path)[1]["prompt"]
    assert collect(directory, child, 2)["research"]["requirements_met"]
    table, _ = synthesize(directory)
    confirm_for_progress(directory, table)
    table, _ = synthesize(directory)
    step = progress(directory, table)
    assert step["state"] == "completed_unverified" and step["research"]["requirements_met"]
    assert step["research"]["observed_rounds"] == [1, 2]
    assert all(row["verification_status"] != "verified" for row in table["rows"])
    export(table, directory)
    assert "saved execution receipts" in (directory / "table.md").read_text()
    assert "research" in (directory / "report.html").read_text()
    with pytest.raises(ValueError, match="outside configured"):
        ask(directory, child, 3, table=table)
    assert len(queues(tmp_path)) == 2


def test_no_new_data_stops_direct_research_ask_despite_valid_receipts(tmp_path):
    directory, child = research_run(tmp_path, rounds=3)
    ask(directory, child, opt_in=True)
    collect(directory, child)
    first, _ = synthesize(directory)
    raw = {provider: read(answer_file(directory, provider))["raw"] for provider in PROVIDERS}
    ask(directory, child, 2, table=first)
    config(tmp_path, answers=raw)
    collect(directory, child, 2)
    table, _ = synthesize(directory)
    assert table["rounds"][-1]["stop"] and table["research"]["requirements_met"]
    with pytest.raises(ValueError, match="stopped the loop"):
        ask(directory, child, 3, table=table)
    assert len(queues(tmp_path)) == 2 and not (directory / "rounds/3/launch.json").exists()


def test_manual_plain_support_cannot_claim_research_evidence(tmp_path):
    directory, child = research_run(tmp_path)
    assert "Synthetic OCR" in prompt(directory, 1)
    import_answer(directory, 1, "human", '{"columns":[],"candidates":[{"name":"Manual","homepage":"https://manual.example","values":{}}]}')
    table, _ = synthesize(directory)
    assert table["rows"] and not table["research"]["requirements_met"]
    assert progress(directory, table)["state"] == "research_mode_unverified"
    with pytest.raises(ValueError, match="matching receipts"):
        ask(directory, child, 2, opt_in=True, table=table)
    assert not queues(tmp_path)


@pytest.mark.parametrize("failure", ["single", "plain", "all", "empty_inventory", "missing_contract", "wrong_settings"])
def test_selection_and_capability_gates_refuse_before_scope_or_queue_effect(tmp_path, failure):
    if failure in {"single", "plain"}:
        selected = copy.deepcopy(TARGETS[:1] if failure == "single" else TARGETS)
        if failure == "plain":
            selected[0].update(kind="plain_chat", mode_id=None, settings={})
        with pytest.raises(ValueError, match="at least two"):
            make_request("synthetic", targets=selected)
        return
    if failure == "all":
        with pytest.raises(ValueError, match="mutually exclusive"):
            make_request("synthetic", providers="all", targets=TARGETS)
        return
    directory, child = research_run(tmp_path)
    if failure == "empty_inventory":
        config(tmp_path, catalog={p: [] for p in PROVIDERS})
    elif failure == "missing_contract":
        config(tmp_path, contracts={"run_lookup_admission": 1, "launch_outcome": 1})
    else:
        changed = copy.deepcopy(TARGETS[0])
        changed["settings"]["strict"] = 1
        config(tmp_path, catalog={PROVIDERS[0]: [changed], PROVIDERS[1]: [TARGETS[1]]})
    with pytest.raises(ValueError, match="child"):
        ask(directory, child, opt_in=True)
    assert not queues(tmp_path) and "outbound_consent" not in read(directory / "request.json")


def test_cli_explicit_target_file_and_dry_run_do_not_create_authority(tmp_path, capsys):
    target_file = tmp_path / "targets.json"
    save(target_file, {"schema_version": 1, "targets": TARGETS})
    assert main(["--project", str(tmp_path), "init", "Synthetic", "--run-id", "typed",
                 "--research-targets-file", str(target_file)]) == 0
    capsys.readouterr()
    directory = tmp_path / ".ihav_space/ihav-competitor-search/runs/typed"
    assert main(["--project", str(tmp_path), "ask", "typed", "--web-chat", str(FAKE), "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["preview"]["targets"] == TARGETS and output["preview"]["round_budget"]["rounds"] == 2
    assert not queues(tmp_path) and "outbound_consent" not in read(directory / "request.json")


@pytest.mark.parametrize("scalar", ["text", True, 1, 1.0, None])
def test_finite_scalar_settings_preserved_without_type_coercion(scalar):
    selected = copy.deepcopy(TARGETS[0])
    selected["settings"] = {"value": scalar}
    assert research.target(selected)["settings"]["value"] is scalar
    assert research.digest({"prompt": "原文\r\n", **selected})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), [], {}, {"nested": 1}])
def test_nonfinite_and_nested_settings_rejected(bad):
    selected = copy.deepcopy(TARGETS[0])
    selected["settings"] = {"value": bad}
    with pytest.raises(ValueError, match="finite JSON scalar"):
        research.target(selected)


def test_canonical_scalar_type_and_trusted_caller_precedence():
    assert not research.exact({"v": True}, {"v": 1})
    assert not research.exact({"v": 1.0}, {"v": 1})
    assert research.trusted_caller({"CLAUDE_CODE_SESSION_ID": "claude", "CODEX_THREAD_ID": "codex"}) == {"host": "claude_code", "session": "claude"}


def test_interrupted_typed_launch_reuses_saved_ingress_scope_without_new_queue(tmp_path, monkeypatch):
    directory, child = research_run(tmp_path)
    original = child.launch_targets
    def crash(*args):
        original(*args)
        raise KeyboardInterrupt()
    monkeypatch.setattr(child, "launch_targets", crash)
    with pytest.raises(KeyboardInterrupt):
        ask(directory, child, opt_in=True)
    saved = read(directory / "rounds/1/launch.json")
    assert saved["state"] == "launching"
    monkeypatch.delenv("CODEX_THREAD_ID")
    recovered = ask(directory, WebChatChild(FAKE, tmp_path))
    assert recovered["authorization"] == saved["authorization"]
    assert recovered["request_key"] == saved["request_key"] and len(queues(tmp_path)) == 1


def test_multiple_exact_descriptors_do_not_widen_requested_settings(tmp_path):
    directory, child = research_run(tmp_path)
    different = copy.deepcopy(TARGETS[0])
    different["mode_id"] = "synthetic-other-mode"
    config(tmp_path, catalog={PROVIDERS[0]: [different, TARGETS[0]], PROVIDERS[1]: [TARGETS[1]]})
    assert ask(directory, child, opt_in=True)["targets"] == TARGETS
    assert queues(tmp_path)[0]["targets"] == TARGETS


def test_saved_receipt_tamper_is_rejected_by_synthesis(tmp_path):
    directory, child = research_run(tmp_path)
    ask(directory, child, opt_in=True)
    collect(directory, child)
    path = answer_file(directory)
    record = read(path)
    record["execution_receipt"]["turn_binding"]["turn"] += 1
    save(path, record)
    with pytest.raises(ValueError, match="receipt changed"):
        synthesize(directory)
    assert len(queues(tmp_path)) == 1


def test_survey_cli_preview_then_single_queue_and_collection_step(tmp_path, capsys):
    target_file = tmp_path / "targets.json"
    save(target_file, {"schema_version": 1, "targets": TARGETS})
    prefix = ["--project", str(tmp_path), "survey"]
    args = ["Synthetic", "--run-id", "typed", "--research-targets-file", str(target_file), "--web-chat", str(FAKE)]
    assert main(prefix + args + ["--dry-run"]) == 0
    assert not (tmp_path / ".ihav_space/ihav-competitor-search").exists() and not queues(tmp_path)
    capsys.readouterr()
    assert main(prefix + args + ["--opt-in"]) == 0
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[-1]["state"] == "awaiting_child" and len(queues(tmp_path)) == 1
    assert main(prefix + ["--run-id", "typed", "--web-chat", str(FAKE)]) == 0
    step = json.loads(capsys.readouterr().out)
    assert step["state"] == "homepage_confirmation_pending" and len(queues(tmp_path)) == 1
    assert step["research"]["rounds"]["1"]["requirements_met"]

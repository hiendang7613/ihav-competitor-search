"""Consumer of WebChat's versioned typed-target wire; never starts a worker.

Authorization expresses persisted trusted-host ingress scope consistency only.
It is not independent human consent, native authorization or provider evidence.
"""
import hashlib
import json
import math
import os
import re
from datetime import datetime

CONTRACTS = {"target_execution": 1, "target_authorization": 1}
FIELD_KINDS = ["domain verbatim", "candidate name", "candidate homepage", "accepted column values"]
BUDGET_KEYS = ("rounds", "max_new", "max_new_columns", "max_total_columns")
BUDGET_DEFAULTS = (2, 30, 5, 25)
MAX_METADATA_BYTES = 1024 * 1024
TARGET_KEYS = {"provider", "kind", "mode_id", "settings"}
AUTH_KEYS = {"schema_version", "effect", "issuer", "issued_at", "request_key",
             "prompt_sha256", "outbound_scope_sha256", "targets"}
RECEIPT_KEYS = {"schema_version", "qualification", "reason", "run_id", "job_id", "provider",
                "prompt_sha256", "outbound_sha256", "requested", "observed", "observation_status",
                "turn_binding", "transport_status", "raw_answer_sha256"}
TRANSPORT = {"queued", "sending", "running", "completed", "failed", "timeout", "login_required",
             "human_verification_required", "not_sent", "sent_unknown", "cancelled"}


def canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid typed-target JSON") from exc


def exact(left, right):
    return canonical_bytes(left) == canonical_bytes(right)


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bounded(value):
    if len(canonical_bytes(value)) > MAX_METADATA_BYTES:
        raise ValueError("research metadata exceeds 1 MiB")
    return value


def timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("research timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError:
        raise ValueError("research timestamp requires an ISO timezone") from None
    return value


def issuer(value):
    if (not isinstance(value, dict) or set(value) != {"host", "session"}
            or value["host"] not in {"claude_code", "codex"}
            or not isinstance(value["session"], str) or not value["session"]):
        raise ValueError("research requires a trusted host issuer")
    return bounded(value)


def trusted_caller(environ=None):
    environ = os.environ if environ is None else environ
    for key, host in (("CLAUDE_CODE_SESSION_ID", "claude_code"), ("CODEX_THREAD_ID", "codex")):
        if environ.get(key):
            return issuer({"host": host, "session": environ[key]})
    raise ValueError("new research dispatch requires trusted host session ingress")


def target(value):
    if (not isinstance(value, dict) or set(value) != TARGET_KEYS
            or not isinstance(value["provider"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value["provider"])
            or value["kind"] not in {"plain_chat", "research"}
            or not isinstance(value["settings"], dict)):
        raise ValueError("invalid typed provider target")
    mode = value["mode_id"]
    if value["kind"] == "research":
        if not isinstance(mode, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", mode):
            raise ValueError("research target requires an explicit mode_id")
    elif mode is not None or value["settings"]:
        raise ValueError("plain_chat target has no mode or settings")
    if any(not isinstance(k, str) or not k or len(k) > 64 or
           (v is not None and type(v) not in {str, int, bool, float}) or
           (type(v) is float and not math.isfinite(v))
           for k, v in value["settings"].items()):
        raise ValueError("target settings require bounded keys and finite JSON scalar values")
    return bounded(value)


def target_set(value, *, research_only=False):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "targets"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["targets"], list) or not value["targets"]):
        raise ValueError("invalid versioned target set")
    targets = [target(item) for item in value["targets"]]
    if len({item["provider"] for item in targets}) != len(targets):
        raise ValueError("target providers must be unique")
    if research_only and (len(targets) < 2 or any(item["kind"] != "research" for item in targets)):
        raise ValueError("research requires at least two explicitly selected research targets")
    return bounded(targets)


def request_targets(request):
    if "targets" not in request:
        if request.get("schema_version", 1) == 2:
            raise ValueError("parent research request is missing targets")
        return None
    if type(request.get("schema_version")) is not int or request["schema_version"] != 2:
        raise ValueError("research targets require parent request schema 2")
    targets = target_set({"schema_version": 1, "targets": request["targets"]}, research_only=True)
    if request.get("providers") != [item["provider"] for item in targets]:
        raise ValueError("research providers must match explicit targets in order")
    return targets


def require_capabilities(caps, targets):
    contracts = caps.get("contracts")
    if not isinstance(contracts, dict) or any(type(contracts.get(k)) is not int or contracts[k] != v
                                            for k, v in CONTRACTS.items()):
        raise ValueError("child lacks versioned target execution/authorization contracts")
    catalog = caps.get("targets")
    if not isinstance(catalog, dict):
        raise ValueError("child has no exact executable target inventory")
    for wanted in targets:
        if wanted["provider"] not in caps.get("providers", []):
            raise ValueError("selected research provider is not supported by the child")
        choices = catalog.get(wanted["provider"])
        if not isinstance(choices, list) or not any(exact(target(item), wanted) for item in choices):
            raise ValueError("child has no exact qualified target for " + wanted["provider"])


def identities(prompt, targets):
    return [{"provider": item["provider"], "outbound_sha256": digest({"prompt": prompt, **item})}
            for item in targets]


def request_key(run_id, number, prompt, targets):
    identity = {"prompt_sha256": text_digest(prompt), "targets": identities(prompt, targets)}
    key = f"{run_id}:{number}:v2:" + digest(identity)
    if len(key) > 256:
        raise ValueError("research request key exceeds child limit")
    return key


def authorization_request(prompt, key, targets):
    scope = {"effect": "web_chat_send", "request_key": key, "prompt_sha256": text_digest(prompt),
             "targets": identities(prompt, targets)}
    return {"schema_version": 1, **scope, "outbound_scope_sha256": digest(scope)}


def validate_authorization(value, *, prompt, key, targets, caller):
    if not isinstance(value, dict) or set(value) != AUTH_KEYS:
        raise ValueError("invalid research authorization shape")
    issuer(value["issuer"])
    timestamp(value["issued_at"])
    actual = {k: v for k, v in value.items() if k not in {"issuer", "issued_at"}}
    if not exact(value["issuer"], caller) or not exact(actual, authorization_request(prompt, key, targets)):
        raise ValueError("research authorization does not bind the exact saved scope")
    return bounded(value)


def budgets(request):
    options = request.get("options", {})
    return {key: options.get(key, default) for key, default in zip(BUDGET_KEYS, BUDGET_DEFAULTS)}


def consent_scope(request, fields, caller):
    targets = request_targets(request)
    if targets is None:
        raise ValueError("research consent requires a typed parent request")
    return {"schema_version": 2, "field_kinds": fields, "providers": request["providers"],
            "domain": request["domain"], "targets": targets, "budgets": budgets(request), "issuer": issuer(caller)}


def validate_consent(request, fields, *, active=False):
    consent = request.get("outbound_consent")
    if not isinstance(consent, dict) or set(consent) != {
            "schema_version", "field_kinds", "providers", "domain", "targets", "budgets", "issuer", "recorded_at"}:
        raise ValueError("research requires exact persisted target/round consent")
    timestamp(consent["recorded_at"])
    expected = consent_scope(request, fields, consent["issuer"])
    if not exact({k: v for k, v in consent.items() if k != "recorded_at"}, expected):
        raise ValueError("research consent settings or round budget changed")
    if active and not exact(consent["issuer"], trusted_caller()):
        raise ValueError("trusted host issuer changed; do not derive another research authorization")
    return consent


def validate_launch_scope(request, launch):
    targets = request_targets(request)
    consent = validate_consent(request, FIELD_KINDS)
    if (type(launch.get("request_schema_version")) is not int or launch["request_schema_version"] != 2
            or not exact(launch.get("targets"), targets)
            or launch.get("providers") != request["providers"]
            or launch.get("domain") != request["domain"]
            or not isinstance(launch.get("authorization"), dict)
            or launch.get("consent_sha256") != digest(consent)
            or not exact(launch["authorization"].get("issuer"), consent["issuer"])
            or launch.get("authorization_sha256") != digest(launch["authorization"])):
        raise ValueError("saved research launch scope changed; never relaunch")
    return targets


def validate_launch_prompt(launch, prompt, parent_run_id, number):
    targets = target_set({"schema_version": 1, "targets": launch.get("targets")}, research_only=True)
    key = request_key(parent_run_id, number, prompt, targets)
    authorization = launch.get("authorization")
    if (launch.get("request_key") != key or launch.get("prompt_sha256") != text_digest(prompt)
            or not isinstance(authorization, dict)
            or launch.get("outbound_scope_sha256") != authorization.get("outbound_scope_sha256")
            or launch.get("authorization_sha256") != digest(authorization)):
        raise ValueError("saved research launch prompt/settings changed; never relaunch")
    validate_authorization(authorization, prompt=prompt, key=key, targets=targets,
                           caller=issuer(authorization.get("issuer")))


def round_summary(launch, answers):
    """Compute mode evidence from immutable answers, not saved summary counts."""
    if launch is None or launch.get("request_schema_version", 1) != 2:
        return {"state": "unverified", "reason": "legacy_or_manual_input", "qualified_providers": [],
                "required_providers": [], "missing_providers": [], "requirements_met": False}
    providers = launch["providers"]
    qualified = []
    for record in answers:
        validate_saved_answer(record, launch)
        if record["status"] == "completed" and record["research_qualification"]["state"] == "observed":
            qualified.append(record["provider"])
    qualified = [provider for provider in providers if provider in qualified]
    missing = [provider for provider in providers if provider not in qualified]
    met = len(providers) >= 2 and not missing
    return {"state": "observed" if met else "unverified", "qualified_providers": qualified,
            "required_providers": providers, "missing_providers": missing, "requirements_met": met,
            "reason": None if met else "research_receipts_incomplete"}


def collection_summary(launch, answers, record):
    summary = round_summary(launch, answers)
    if launch is None or launch.get("request_schema_version", 1) != 2:
        return summary
    statuses = record.get("providers") if isinstance(record, dict) else None
    ingestion = record.get("ingestion") if isinstance(record, dict) else None
    if (not isinstance(statuses, dict) or set(statuses) != set(launch["providers"])
            or not isinstance(ingestion, dict) or set(ingestion) != set(launch["providers"])
            or record.get("child_run_id") != launch.get("child_run_id")
            or record.get("state") not in {"completed", "partial"} or record.get("stop_reason")):
        summary.update(state="unverified", reason="research_collection_unresolved", qualified_providers=[],
                       missing_providers=launch["providers"], requirements_met=False)
        return summary
    summary["qualified_providers"] = [provider for provider in summary["qualified_providers"]
                                      if isinstance(statuses[provider], dict) and statuses[provider].get("status") == "completed"
                                      and isinstance(ingestion[provider], dict) and ingestion[provider].get("status") == "completed"]
    summary["missing_providers"] = [provider for provider in launch["providers"] if provider not in summary["qualified_providers"]]
    summary["requirements_met"] = not summary["missing_providers"] and len(launch["providers"]) >= 2
    summary["state"] = "observed" if summary["requirements_met"] else "unverified"
    summary["reason"] = None if summary["requirements_met"] else "research_receipts_incomplete"
    return summary


def validate_receipt(value, launch, provider, raw):
    if (not isinstance(value, dict) or set(value) != RECEIPT_KEYS
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value["qualification"] not in {"qualified", "unqualified"}
            or (value["reason"] is not None and not isinstance(value["reason"], str))
            or value["observation_status"] not in {"matched", "missing", "mismatch"}
            or value["transport_status"] not in TRANSPORT):
        raise ValueError("invalid execution receipt schema")
    wanted = next((item for item in launch["targets"] if item["provider"] == provider), None)
    outbound = next(item["outbound_sha256"] for item in launch["authorization"]["targets"]
                    if item["provider"] == provider)
    if (wanted is None or value["run_id"] != launch["child_run_id"]
            or value["job_id"] != launch["child_run_id"] + "/" + provider or value["provider"] != provider
            or value["prompt_sha256"] != launch["prompt_sha256"] or value["outbound_sha256"] != outbound
            or not exact(target(value["requested"]), wanted)):
        raise ValueError("execution receipt identity does not match the saved launch")
    if value["observed"] is not None:
        target(value["observed"])
    binding = value["turn_binding"]
    if binding is not None and (not isinstance(binding, dict) or set(binding) != {"conversation_url", "turn", "marker"}
            or type(binding["turn"]) is not int or binding["turn"] < 1
            or any(binding[k] is not None and not isinstance(binding[k], str) for k in ("conversation_url", "marker"))
            or not (binding["conversation_url"] or binding["marker"])):
        raise ValueError("invalid execution receipt turn binding")
    raw_hash = value["raw_answer_sha256"]
    if raw_hash is not None and (not isinstance(raw_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_hash)
                                 or raw_hash != text_digest(raw)):
        raise ValueError("execution receipt raw answer hash mismatch")
    if value["qualification"] == "qualified" and (
            value["reason"] is not None or value["observation_status"] != "matched"
            or not exact(value["observed"], wanted) or binding is None
            or value["transport_status"] != "completed" or raw_hash is None):
        raise ValueError("execution receipt cannot qualify this research answer")
    return bounded(value)


def qualification(receipt, launch, provider, raw):
    if receipt is None:
        return {"state": "unverified", "reason": "execution_receipt_missing"}
    try:
        valid = validate_receipt(receipt, launch, provider, raw)
    except (ValueError, TypeError, KeyError, StopIteration):
        return {"state": "unverified", "reason": "execution_receipt_invalid"}
    if valid["qualification"] != "qualified":
        return {"state": "unverified", "reason": "execution_receipt_unqualified"}
    return {"state": "observed", "reason": None}


def validate_saved_answer(record, launch):
    if launch.get("request_schema_version", 1) != 2:
        if any(k in record for k in ("execution_receipt", "execution_receipt_sha256", "research_qualification")):
            raise ValueError("legacy answer cannot acquire typed research attribution")
        return record
    receipt = record.get("execution_receipt")
    if (set(("execution_receipt", "execution_receipt_sha256", "research_qualification")) - set(record)
            or record["execution_receipt_sha256"] != digest(bounded(receipt))
            or not exact(record["research_qualification"], qualification(receipt, launch, record["provider"], record["raw"]))):
        raise ValueError("saved research answer receipt changed")
    return record

"""Bounded survey planning from saved evidence; never starts a worker or network call."""
import re
import secrets
from datetime import datetime, timezone

from .chatbots import read, save
from .merge import CORE
from .visits import source_blocked
from . import research


def run_root(project):
    project = project.resolve()
    root = project / ".ihav_space" / "ihav-competitor-search" / "runs"
    if root.resolve() != root:
        raise ValueError("run root must not contain symlinks")
    return root


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("invalid run id")
    return value


def make_request(domain, *, providers=None, targets=None, rounds=2,
               max_new=30, max_new_columns=5, max_total_columns=25,
               max_lookups=100, verify_top=50, no_verify=False):
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be nonempty text")
    options = {"rounds": rounds, "max_new": max_new, "max_new_columns": max_new_columns,
               "max_total_columns": max_total_columns, "max_lookups": max_lookups,
               "verify_top": verify_top, "no_verify": no_verify}
    for key, value in options.items():
        if key == "no_verify":
            if type(value) is not bool:
                raise ValueError("no_verify must be a boolean")
        elif type(value) is not int or value < (0 if key in {"max_new_columns", "max_lookups"} else 1):
            raise ValueError(f"invalid {key}")
    if max_total_columns < len(CORE):
        raise ValueError("max_total_columns must include all core columns")
    if targets is not None:
        if providers is not None:
            raise ValueError("research targets and --providers are mutually exclusive")
        targets = research.target_set({"schema_version": 1, "targets": targets}, research_only=True)
        if rounds < 2:
            raise ValueError("research requires a budget for at least two successive rounds")
        return {"schema_version": 2, "domain": domain, "providers": [item["provider"] for item in targets],
                "targets": targets, "options": options}
    providers = ["chatgpt", "gemini", "perplexity"] if providers is None else providers
    if providers != "all" and (not isinstance(providers, list) or not providers
            or any(not isinstance(p, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", p) for p in providers)
            or len(set(providers)) != len(providers)):
        raise ValueError("providers must be a nonempty unique list or all")
    return {"schema_version": 1, "domain": domain, "providers": providers, "options": options}


def create_run(project, domain, *, run_id=None, **options):
    request = make_request(domain, **options)
    identifier = safe_id(run_id) if run_id is not None else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)
    root = run_root(project)
    directory = root / identifier
    root.mkdir(parents=True, exist_ok=True)
    directory.mkdir()  # Never replace or merge an existing run.
    save(directory / "request.json", request)
    return directory


def _progress(directory, table):
    """Return the next action; a complete export is distinct from checked facts."""
    request = read(directory / "request.json")
    targets = research.request_targets(request)
    options = request.get("options", {})
    limit = options.get("rounds", 2)
    if type(limit) is not int or limit < 1:
        raise ValueError("rounds must be a positive integer")
    summaries = {r["round"]: r for r in table["rounds"]}
    for number in range(1, limit + 1):
        folder = directory / "rounds" / str(number)
        launch = read(folder / "launch.json") if (folder / "launch.json").exists() else None
        child = table.get("chatbot_rounds", {}).get(str(number))
        if launch is not None:
            if launch.get("state") == "refused":
                return {"state": "launch_refused", "action": "new_run", "round": number,
                        "reason": launch.get("reason", "refused_before_queue_admission")}
            if not launch.get("child_run_id"):
                return {"state": "unknown", "action": "collect", "round": number,
                        "reason": "launch_outcome_unknown"}
            if not child or child.get("state") == "waiting" or "parsed_answers" not in child:
                return {"state": "awaiting_child", "action": "collect", "round": number}
            if child.get("state") == "partial_unresolved":
                return {"state": "unresolved_send", "action": "collect", "round": number,
                        "reason": "sent_unknown_requires_reconciliation"}
            if targets is not None and child.get("state") == "reconciliation_required":
                return {"state": "research_attribution_unresolved", "action": "stop", "round": number,
                        "reason": child.get("stop_reason", "delivery_identity_changed")}
            if targets is not None and not table.get("research", {}).get("rounds", {}).get(str(number), {}).get("requirements_met"):
                return {"state": "research_mode_unverified", "action": "stop", "round": number,
                        "reason": "matching_research_receipts_required_for_all_selected_providers"}
            if child.get("parsed_answers", 0) == 0:
                if number == 1:
                    return {"state": "zero_usable_answers", "action": "stop", "round": number,
                            "reason": child.get("stop_reason", "zero_parsed_round_1")}
                break
        elif number not in summaries:
            return {"state": "ready_to_queue", "action": "ask", "round": number}
        summary = summaries.get(number)
        if targets is not None and launch is None and summary:
            return {"state": "research_mode_unverified", "action": "stop", "round": number,
                    "reason": "manual_or_legacy_input_has_no_research_mode_evidence"}
        if not summary:
            return {"state": "zero_usable_answers", "action": "stop", "round": number}
        if not table["rows"] and summary.get("stop"):
            return {"state": "zero_usable_answers", "action": "stop", "round": number}
        decisions = request.get("homepage_decisions", {})
        undecided = [r["candidate_id"] for r in table["rows"]
                     if r["candidate_id"] not in decisions]
        if undecided:
            return {"state": "homepage_confirmation_pending", "action": "confirm",
                    "round": number, "candidate_ids": undecided}
        visits = read(directory / "visits.json") if (directory / "visits.json").exists() else {}
        unknown = [host for host, item in visits.items() if item.get("state") in {"launching", "unknown"}]
        if unknown:
            return {"state": "traffic_outcome_unknown", "action": "reconcile", "hosts": unknown}
        eligible = sorted({r["lookup_host"] for r in table["rows"] if r.get("homepage_status") == "confirmed"})
        attempts = sum(item.get("exit_code") is not None or item.get("state") == "unknown"
                       for item in visits.values())
        if (not any(source_blocked(item) for item in visits.values())
                and attempts < options.get("max_lookups", 100)):
            missing = [host for host in eligible if visits.get(host, {}).get("exit_code") is None]
            if missing:
                return {"state": "traffic_lookup_pending", "action": "lookup", "round": number,
                        "hosts": missing}
        if summary.get("stop"):
            break
    if options.get("no_verify", False):
        return {"state": "completed_unverified", "action": "render", "reason": "verification_disabled"}
    scope = table["rows"][:options.get("verify_top", 50)]
    unconfirmed = [r["candidate_id"] for r in scope if r.get("homepage_status") != "confirmed"]
    outstanding = [r["candidate_id"] for r in scope if r.get("homepage_status") == "confirmed"
                   and r.get("verification_status") != "verified"]
    if outstanding:
        return {"state": "verification_pending", "action": "verify", "candidate_ids": outstanding,
                "unverified_candidate_ids": unconfirmed}
    if unconfirmed:
        return {"state": "completed_unverified", "action": "render", "reason": "unconfirmed_homepages",
                "unverified_candidate_ids": unconfirmed, "verification_scope": len(scope),
                "outside_scope": max(0, len(table["rows"]) - len(scope))}
    return {"state": "completed_with_recorded_evidence", "action": "render",
            "verification_scope": len(scope), "outside_scope": max(0, len(table["rows"]) - len(scope))}


def progress(directory, table):
    result = _progress(directory, table)
    if research.request_targets(read(directory / "request.json")) is not None:
        result["research"] = table.get("research", {"state": "unverified", "requirements_met": False})
    return result

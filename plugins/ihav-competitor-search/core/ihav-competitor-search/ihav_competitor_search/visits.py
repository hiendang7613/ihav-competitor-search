"""Durable per-host counter subprocess adapter; no source fetch logic here."""
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from .render import atomic_write


def source_blocked(record):
    """Use v2 provider outcomes; retain the legacy exit/prose rule for v1."""
    payload = record.get("result") or record.get("raw_json") or {}
    version = payload.get("contract_version") if isinstance(payload, dict) else None
    if isinstance(version, int) and not isinstance(version, bool) and version >= 2:
        providers = payload.get("providers", [])
        return isinstance(providers, list) and any(
            isinstance(provider, dict) and provider.get("name") == "webtrafficchecker"
            and provider.get("outcome") == "blocked" for provider in providers)
    if record.get("exit_code") == 4 or record.get("primary_blocked") is True:
        return True
    notes = payload.get("notes", []) if isinstance(payload, dict) else []
    if not isinstance(notes, list):
        return False
    for note in notes:
        if not isinstance(note, str):
            continue
        match = re.search(r"WebTrafficChecker failed:\s*([^;]*)", note, re.I)
        if match and re.search(r"\bHTTP\s+(?:401|403|429)\b|challenge page|\bblocked\b|rate.limit", match.group(1), re.I):
            return True
    return False


def counter_path(explicit=None):
    value = explicit or os.environ.get("IHAV_VISIT_COUNTER")
    path = Path(value).expanduser().resolve() if value else None
    if path is None or not path.is_file():
        raise ValueError("Counter script not found. Install ihav-web-visit-counter, restart the host, then set --counter PATH or IHAV_VISIT_COUNTER to scripts/visits.py.")
    return path


class CounterVisitStage:
    def __init__(self, counter, project, timeout=120):
        self.counter = counter_path(counter)
        self.project = Path(project).resolve()
        self.timeout = timeout

    def lookup(self, lookup_host):
        # Explicit cache path prevents IHAV_CACHE_DIR from moving child files.
        command = [sys.executable, str(self.counter), lookup_host, "--json", "--cache-dir",
                   str(self.project / ".ihav_space" / "ihav-web-visit-counter")]
        result = subprocess.run(command, cwd=self.project, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=self.timeout)
        record = {"exit_code": result.returncode, "counter_exit_code": result.returncode,
                  "raw_stdout": result.stdout, "raw_stderr": result.stderr, "state": "completed"}
        try:
            payload = json.loads(result.stdout, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid number {v}")))
            if not isinstance(payload, dict):
                raise ValueError("counter JSON must be an object")
            record["raw_json"] = payload
        except ValueError as exc:
            payload = None
            record["protocol_error"] = str(exc)
        if result.returncode == 0 and payload is not None:
            count = payload.get("monthly_visits")
            valid_estimate = (payload.get("kind") == "estimate" and
                              ((isinstance(count, int) and not isinstance(count, bool) and count >= 0)
                               or (count is None and isinstance(payload.get("monthly_visits_text"), str)
                                   and bool(payload["monthly_visits_text"].strip()))))
            rank = payload.get("rank")
            rank_value = rank.get("value") if isinstance(rank, dict) else None
            valid_rank = (payload.get("kind") == "rank_only" and count is None
                          and isinstance(rank_value, int) and not isinstance(rank_value, bool) and rank_value > 0)
            if payload.get("domain") == lookup_host and (valid_estimate or valid_rank):
                record["result"] = payload
            else:
                record.update(exit_code=5, reason="invalid_counter_response", protocol_error="result contract mismatch")
        elif result.returncode not in {2, 4, 5, 64} or payload is None:
            record["exit_code"] = 5
            record["reason"] = "invalid_counter_response"
        if result.returncode in {2, 4, 5, 64} and payload is not None:
            error = payload.get("error", {})
            record["reason"] = error.get("message", "counter_error") if isinstance(error, dict) else "counter_error"
        # Even malformed stdout must retain a source block as exit 4.
        if result.returncode == 4:
            record["exit_code"] = 4
        record["primary_blocked"] = source_blocked(record)
        return record


@contextmanager
def lookup_lock(directory):
    lock = directory / "lookup.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("Lookup lock exists. Confirm the previous process has stopped before removing lookup.lock; no subprocess was started.") from None
    try:
        yield
    finally:
        lock.rmdir()


def lookup_hosts(table, directory, stage, *, max_lookups=100, eligible_hosts=None):
    """Persist intent before dispatch and every outcome before the next host.

    Existing terminal responses are never retried. Interrupted intents become
    unresolved and prevent any further dispatch. The cap counts historical
    dispatches, even for hosts removed from the latest synthesis.
    """
    if isinstance(max_lookups, bool) or not isinstance(max_lookups, int) or max_lookups < 0:
        raise ValueError("max-lookups must be a nonnegative integer")
    path = directory / "visits.json"
    with lookup_lock(directory):
        visits = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(visits, dict) or any(not isinstance(v, dict) for v in visits.values()):
            raise ValueError("visits must map lookup hosts to response objects")
        def save():
            atomic_write(path, json.dumps(visits, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        unknown = False
        for record in visits.values():
            if record.get("state") in {"launching", "unknown"}:
                record.update(state="unknown", reason="lookup_outcome_unknown")
                unknown = True
        blocked = any(source_blocked(record) for record in visits.values())
        attempts = sum(v.get("exit_code") is not None or v.get("state") == "unknown" for v in visits.values())
        hosts = list(dict.fromkeys(row["lookup_host"] for row in table["rows"]))
        for host in hosts:
            previous = visits.get(host, {})
            if previous.get("exit_code") is not None or previous.get("state") == "unknown":
                continue
            reason = ("lookup_outcome_unknown" if unknown else "blocked" if blocked
                      else "homepage_unconfirmed" if eligible_hosts is not None and host not in eligible_hosts
                      else "lookup_cap" if attempts >= max_lookups else None)
            if reason:
                visits[host] = {"exit_code": None, "reason": reason, "visits_unavailable": reason, "state": "skipped"}
                continue
            visits[host] = {"exit_code": None, "state": "launching"}
            save()
            attempts += 1
            try:
                visits[host] = stage.lookup(host)
            except (subprocess.TimeoutExpired, OSError) as exc:
                visits[host] = {"exit_code": None, "state": "unknown", "reason": "lookup_outcome_unknown",
                                "detail": type(exc).__name__}
                unknown = True
            blocked = blocked or source_blocked(visits[host])
            save()
        save()
        return visits

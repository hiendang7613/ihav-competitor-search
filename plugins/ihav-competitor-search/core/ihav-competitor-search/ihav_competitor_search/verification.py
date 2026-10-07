"""Record host-supplied official-page checks; never fetch or infer a fact.

``verified`` describes a host/human check backed by the submitted page excerpt.
The deterministic module validates attribution and types, not the truth of the
excerpt or the host's interpretation. Missing values remain individually
unverified even when every filled cell in a row has been checked.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime
from pathlib import Path

from .locking import ask_lock
from .homepages import RAW_BYTES, now
from .merge import MAX_JSON_DEPTH, TYPES, normalize_url, signature, type_fits
from .render import atomic_write

DEFAULT_TOP = 50
MAX_INPUT_BYTES = RAW_BYTES * 8
MAX_STORE_BYTES = 128 * 1024 * 1024
STORE_NAME = "cell_verifications.json"
BASIS = "host_supplied_page_evidence"
IDENTITY_COLUMNS = {"name", "homepage", "domain"}
REQUIRED = {"schema_version", "candidate_id", "column", "value", "type",
            "source_url", "fetched_at", "method", "raw_excerpt", "checked_by"}
OPTIONAL = {"unit", "verification", "reason", "notes"}


def _finite_json(value, depth=1):
    if isinstance(value, (dict, list)) and depth > MAX_JSON_DEPTH:
        raise ValueError(f"JSON nesting exceeds the supported limit of {MAX_JSON_DEPTH} container levels")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("verification evidence contains a non-finite number")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("verification evidence JSON keys must be strings")
        for child in value.values():
            _finite_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _finite_json(child, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("verification evidence must contain only JSON values")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate verification JSON key: " + key)
        result[key] = value
    return result


def _load_json(path, limit):
    with Path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("verification JSON exceeds the byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=lambda word: (_ for _ in ()).throw(
                               ValueError("invalid JSON number: " + word)))
    except (UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("verification evidence must be bounded UTF-8 JSON") from exc
    _finite_json(value)
    return value


def load_evidence(path):
    """Read one bounded local JSON object; no network or saved-state mutation."""
    return _payload(_load_json(path, MAX_INPUT_BYTES))


def _payload(evidence):
    if not isinstance(evidence, dict) or not REQUIRED <= evidence.keys():
        raise ValueError("verification evidence is missing required fields")
    if evidence.keys() - REQUIRED - OPTIONAL:
        raise ValueError("verification evidence contains unknown fields")
    _finite_json(evidence)
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
        raise ValueError("unsupported verification evidence schema_version")
    for key in ("candidate_id", "column", "type", "source_url", "fetched_at", "raw_excerpt"):
        if not isinstance(evidence[key], str) or not evidence[key].strip():
            raise ValueError("verification evidence needs a nonempty " + key)
    if evidence["method"] != "page":
        raise ValueError("verification method must be page")
    if evidence["checked_by"] not in ("host", "human"):
        raise ValueError("verification checked_by must be host or human")
    try:
        fetched = datetime.fromisoformat(evidence["fetched_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("verification fetched_at must be an ISO timestamp") from exc
    if fetched.utcoffset() is None:
        raise ValueError("verification fetched_at must include a timezone")
    if len(evidence["raw_excerpt"].encode("utf-8")) > RAW_BYTES:
        raise ValueError("verification raw_excerpt exceeds the byte limit")
    source = evidence["source_url"]
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in source):
        raise ValueError("verification source_url contains whitespace or control characters")
    normalize_url(source)
    result = copy.deepcopy(evidence)
    result.setdefault("verification", "verified")
    result.setdefault("reason", None)
    if result["verification"] not in ("verified", "partial", "unverified"):
        raise ValueError("unsupported cell verification status")
    if result["verification"] == "verified":
        if result["value"] is None or (isinstance(result["value"], str) and not result["value"].strip()):
            raise ValueError("verified cell needs a nonempty checked value")
        if result["reason"] is not None:
            raise ValueError("verified cell must not contain an unresolved reason")
    elif not isinstance(result["reason"], str) or not result["reason"].strip():
        raise ValueError("partial or unverified evidence needs a reason")
    if "notes" in result and (not isinstance(result["notes"], str)
                              or len(result["notes"].encode("utf-8")) > RAW_BYTES):
        raise ValueError("verification notes must be bounded text")
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("verification evidence exceeds the byte limit")
    return result


def _scope(table, top):
    if type(top) is not int or top < 1:
        raise ValueError("verification top must be a positive integer")
    return {row["candidate_id"] for row in table["rows"][:top]}


def _target(table, evidence):
    row = next((item for item in table["rows"]
                if item["candidate_id"] == evidence["candidate_id"]), None)
    if row is None:
        raise ValueError("unknown verification candidate_id")
    column = next((item for item in table["columns"] if item["key"] == evidence["column"]), None)
    if column is None:
        raise ValueError("unknown verification column")
    if row.get("homepage_status") != "confirmed":
        raise ValueError("verification requires a confirmed official homepage")
    if normalize_url(evidence["source_url"])[1] != row["lookup_host"]:
        raise ValueError("verification source_url does not match confirmed lookup_host")
    if evidence["type"] != column["type"] or evidence.get("unit", column.get("unit")) != column.get("unit"):
        raise ValueError("verification type or unit differs from the accepted column")
    if not type_fits(evidence["value"], column["type"]):
        raise ValueError("verification value does not fit the accepted column type")
    current = row["cells"][column["key"]]["value"]
    if column["key"] in IDENTITY_COLUMNS and evidence["value"] is not None and evidence["value"] != current:
        raise ValueError("identity correction requires the homepage/identity workflow")
    return row, column


def _store_path(directory):
    directory = Path(directory)
    path = directory / STORE_NAME
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("verification store escapes the run")
    return path


def _read_records(directory):
    path = _store_path(directory)
    if not path.exists():
        return []
    store = _load_json(path, MAX_STORE_BYTES)
    if (not isinstance(store, dict) or set(store) != {"schema_version", "records"}
            or type(store["schema_version"]) is not int or store["schema_version"] != 1
            or not isinstance(store["records"], list)):
        raise ValueError("unsupported or malformed cell verification store")
    identities = set()
    fields = {"evidence", "lookup_host", "column_signature", "recorded_at",
              "original_raw_value", "original_raw_unit", "basis"}
    for record in store["records"]:
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("malformed saved cell verification")
        record["evidence"] = _payload(record["evidence"])
        if (not isinstance(record["lookup_host"], str) or not record["lookup_host"]
                or not isinstance(record["column_signature"], list) or len(record["column_signature"]) != 3
                or not isinstance(record["recorded_at"], str) or not record["recorded_at"]
                or record["basis"] != BASIS):
            raise ValueError("malformed saved cell verification provenance")
        meaning, kind, unit = record["column_signature"]
        if (not isinstance(meaning, str) or not meaning.strip() or not isinstance(kind, str)
                or kind not in TYPES or unit is not None and not isinstance(unit, str)):
            raise ValueError("malformed saved verification column signature")
        identity = (record["evidence"]["candidate_id"], record["evidence"]["column"])
        if identity in identities:
            raise ValueError("duplicate saved cell verification")
        identities.add(identity)
    return store["records"]


def record_cell_evidence(table, directory, evidence, *, replace=False, top=DEFAULT_TOP):
    """Validate and atomically record one supplied cell check in the ranked scope.

    Repeating identical evidence is idempotent. Different evidence for the same
    candidate/column requires explicit replacement; rejected input changes no
    saved record. Homepage/domain/name corrections never occur through this API.
    """
    scope = _scope(table, top)
    evidence = _payload(evidence)
    row, column = _target(table, evidence)
    if row["candidate_id"] not in scope:
        raise ValueError("verification candidate is outside the requested top scope")
    evidence.setdefault("unit", column.get("unit"))
    with ask_lock(Path(directory)):
        records = _read_records(directory)
        identity = evidence["candidate_id"], evidence["column"]
        previous = next((record for record in records
                         if (record["evidence"]["candidate_id"], record["evidence"]["column"]) == identity), None)
        column_signature = list(signature(column))
        if previous is not None:
            if (previous["evidence"] == evidence and previous["lookup_host"] == row["lookup_host"]
                    and previous["column_signature"] == column_signature):
                return copy.deepcopy(previous)
            if not replace:
                raise ValueError("cell evidence already recorded; use --replace to replace it")
            records.remove(previous)
        record = {"evidence": evidence, "lookup_host": row["lookup_host"],
                  "column_signature": column_signature, "recorded_at": now(), "basis": BASIS,
                  "original_raw_value": copy.deepcopy(row["cells"][column["key"]].get("raw_value")),
                  "original_raw_unit": row["cells"][column["key"]].get("raw_unit")}
        records.append(record)
        content = json.dumps({"schema_version": 1, "records": records},
                             ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if len(content.encode("utf-8")) > MAX_STORE_BYTES:
            raise ValueError("cell verification store exceeds the byte limit")
        atomic_write(_store_path(directory), content)
        return copy.deepcopy(record)


def apply_verifications(table, directory, *, top=DEFAULT_TOP):
    """Apply saved local checks to a copy AFTER homepage decisions and ranking.

    Checks tied to a former official host or column meaning remain saved but are
    reported stale. Rows outside the requested prefix never acquire verification
    from these records. Partial/unverified evidence cannot change a value.
    """
    table = copy.deepcopy(table)
    scope = _scope(table, top)
    records = _read_records(directory)
    applied = 0
    for record in records:
        evidence = record["evidence"]
        if evidence["candidate_id"] not in scope:
            continue
        try:
            row, column = _target(table, evidence)
            if record["lookup_host"] != row["lookup_host"] or record["column_signature"] != list(signature(column)):
                raise ValueError("recorded official host or column meaning has changed")
        except ValueError as exc:
            table["issues"].append({"reason": "stale_cell_verification", "candidate_id": evidence["candidate_id"],
                                    "column": evidence["column"], "detail": str(exc)})
            continue
        target = row["cells"][evidence["column"]]
        if evidence["verification"] == "verified":
            target["value"] = copy.deepcopy(evidence["value"])
        target.update(source_url=evidence["source_url"], fetched_at=evidence["fetched_at"], method="page",
                      verification=evidence["verification"], reason=evidence["reason"],
                      verification_basis=BASIS, checked_by=evidence["checked_by"],
                      checked_value=copy.deepcopy(evidence["value"]),
                      raw_excerpt=evidence["raw_excerpt"], evidence=copy.deepcopy(record))
        applied += 1
    for row in table["rows"]:
        if row["candidate_id"] not in scope:
            continue
        filled = [entry for entry in row["cells"].values()
                  if entry["value"] is not None and (not isinstance(entry["value"], str) or entry["value"].strip())]
        if filled and all(entry["verification"] == "verified" for entry in filled):
            row["verification_status"] = "verified"
        elif any(entry["verification"] in {"verified", "partial"} for entry in row["cells"].values()):
            row["verification_status"] = "partial"
        else:
            row["verification_status"] = "unverified"
    table["verification"] = {"basis": BASIS, "top": top, "scope": "top_ranked_rows",
                             "candidate_ids": [row["candidate_id"] for row in table["rows"][:top]],
                             "recorded_cells": len(records), "applied_cells": applied,
                             "unapplied_cells": len(records) - applied}
    return table

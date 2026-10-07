"""Conservative identity matching, typed columns and traceable cells."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from . import research

CORE = {
    "name": "text", "homepage": "url", "domain": "text", "category": "category",
    "description": "text", "pricing_model": "category", "free_tier": "bool",
    "open_source": "bool", "founded": "number", "headquarters": "text",
}
TYPES = {"number", "bool", "category", "text", "url"}
MAX_JSON_DEPTH = 64
RESERVED = {"candidate_id", "traffic_rank", "lookup_host", "monthly_visits", "tranco_rank",
            "lookup_status", "rank_basis", "shared_domain", "verification_status", "mentioned_by",
            "traffic_kind", "analyzed_at", "traffic_source", "lookup_reason", "homepage_status"}
RESERVED |= {"monthly_visits_text", "stale", "scraped_at"}


def normalize_url(value):
    if not isinstance(value, str):
        raise ValueError("homepage must be an HTTP(S) URL")
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("homepage must be an HTTP(S) URL without credentials")
    host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = f"[{host}]" if ":" in host else host
    if port and port != (443 if parts.scheme.lower() == "https" else 80):
        netloc += f":{port}"
    # Preserve product-specific path and query. Host identity is never product identity.
    url = urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/"), parts.query, ""))
    return url, host


def empty_table():
    return {"schema_version": 1, "columns": [
        {"key": key, "label": key.replace("_", " ").title(), "meaning": key,
         "type": kind, "unit": None, "added_in_round": 0, "proposed_by": []}
        for key, kind in CORE.items()], "rows": [], "rounds": [], "issues": []}


def signature(column):
    return (column["meaning"].strip().casefold(), column["type"], column.get("unit"))


def type_fits(value, kind):
    if value is None:
        return True
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "number":
        return (isinstance(value, int) and not isinstance(value, bool)
                or isinstance(value, float) and math.isfinite(value))
    if not isinstance(value, str):
        return False
    if kind == "url":
        try:
            normalize_url(value)
        except ValueError:
            return False
    return True


def cell(value=None, *, kind="text", unit=None, source=None, fetched_at=None):
    fits = type_fits(value, kind)
    return {"value": value if fits else None, "raw_value": value, "raw_unit": unit,
            "source_url": source, "fetched_at": fetched_at, "method": "chatbot" if source else None,
            "verification": "unverified", "reason": "type_mismatch" if not fits else ("missing" if value is None else None)}


def validate_json_value(value, *, max_depth=MAX_JSON_DEPTH):
    """Bound containers before recursive consumers, independent of the decoder."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list, tuple)):
            if depth > max_depth:
                raise ValueError(f"JSON nesting exceeds the supported limit of {max_depth} container levels")
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children)
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def parse_answer(raw):
    if isinstance(raw, dict):
        result = raw
    elif isinstance(raw, str):
        match = re.fullmatch(r"\s*```(?:json)?\s*\n?(.*?)\n?```\s*", raw, re.S)
        try:
            result = json.loads(match.group(1) if match else raw,
                                parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {v}")))
        except RecursionError as exc:
            raise ValueError("answer JSON nesting exceeds the supported limit") from exc
    else:
        raise ValueError("answer must be a JSON object or string")
    if not isinstance(result, dict) or not isinstance(result.get("columns"), list) or not isinstance(result.get("candidates"), list):
        raise ValueError("answer needs columns and candidates arrays")
    # Check the durable UTF-8 representation, including overflowing exponents
    # and escaped unpaired surrogates; dictionary inputs need the same boundary.
    try:
        validate_json_value(result)
    except RecursionError as exc:
        raise ValueError("answer JSON nesting exceeds the supported limit") from exc
    return result


def validate_answer_record(record, provider, *, launch=None):
    """Bind durable provenance to its filename before synthesis or resumption."""
    if (not isinstance(record, dict) or not isinstance(provider, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", provider)
            or record.get("provider") != provider or "raw" not in record):
        raise ValueError("invalid saved answer identity")
    method = record.get("method")
    if (method is not None and not isinstance(method, str)) or method not in {None, "chatbot", "manual_paste", "web_chat_delivery"}:
        raise ValueError("invalid saved answer method")
    if launch is not None and method != "web_chat_delivery":
        raise ValueError("saved answer cannot mix manual or legacy input with a child launch")
    # v0.1 records had no status or child identity. Keep that explicit format.
    if "status" not in record:
        if set(record) - {"provider", "raw", "method", "fetched_at"} or method == "web_chat_delivery":
            raise ValueError("invalid legacy answer record")
        return record
    raw = record["raw"]
    if (method not in {"manual_paste", "web_chat_delivery"} or not isinstance(raw, str)
            or not isinstance(record.get("status"), str) or record["status"] not in {"completed", "parse_failed"}
            or not isinstance(record.get("fetched_at"), str) or not record["fetched_at"]):
        raise ValueError("invalid canonical answer record")
    try:
        timestamp = datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timezone missing")
    except ValueError:
        raise ValueError("invalid saved answer fetched_at; an ISO timestamp with timezone is required") from None
    try:
        parse_answer(raw)
        actual_status = "completed"
    except (ValueError, TypeError, RecursionError):
        actual_status = "parse_failed"
    if record["status"] != actual_status:
        raise ValueError("saved answer status does not match raw input")
    if method == "web_chat_delivery":
        child_id = record.get("child_run_id")
        match = re.fullmatch(r"(\d{8}T\d{6})-[0-9a-f]{8}", child_id) if isinstance(child_id, str) else None
        try:
            if match is None:
                raise ValueError("invalid child identity")
            datetime.strptime(match[1], "%Y%m%dT%H%M%S")
        except ValueError:
            raise ValueError("invalid saved answer child identity") from None
        if (not isinstance(launch, dict) or launch.get("state") != "launched"
                or child_id != launch.get("child_run_id")
                or provider not in launch.get("providers", [])
                or not isinstance(record.get("request_key"), str) or not record["request_key"]
                or record["request_key"] != launch.get("request_key")
                or record.get("raw_sha256") != hashlib.sha256(raw.encode("utf-8")).hexdigest()):
            raise ValueError("saved answer child provenance does not match its launch")
        research.validate_saved_answer(record, launch)
    return record


def merge_round(table, answers, round_number, *, max_new=30, max_new_columns=5, max_total_columns=25):
    """Return a new table; answers carry provider, raw and optional fetched_at.

    No fuzzy/LLM matching or currency conversion is performed. Same name AND
    normalized product URL merges; possible aliases stay separate for host review.
    Repeated candidates add mentions/evidence but do not backfill old rows.
    """
    if type(max_new) is not int or max_new < 1:
        raise ValueError("invalid candidate budget")
    if (isinstance(max_new_columns, bool) or not isinstance(max_new_columns, int)
            or isinstance(max_total_columns, bool) or not isinstance(max_total_columns, int)
            or max_new_columns < 0 or max_total_columns < len(CORE)):
        raise ValueError("invalid column budgets")
    table = copy.deepcopy(table)
    columns = table["columns"]
    rows = table["rows"]
    # Later rounds propose only new columns. Values may use the accepted schema
    # already sent in their prompt, without repeating its declarations.
    accepted_keys = {c["key"] for c in columns}
    before = len(rows), len(columns)
    outcomes = []
    accepted_by_provider = {}
    for envelope in answers:
        provider = envelope["provider"]
        mention = {"provider": provider, "round": round_number}
        method = "manual_paste" if envelope.get("method") == "manual_paste" else "chatbot"
        if method == "manual_paste":
            mention["method"] = method
        source = f"{method}:{provider}:r{round_number}"
        try:
            answer = parse_answer(envelope["raw"])
        except (ValueError, TypeError) as exc:
            failed = {**mention, "status": "parse_failed", "raw": envelope["raw"], "reason": str(exc)}
            try:
                validate_json_value(failed["raw"])
            except (ValueError, TypeError, RecursionError):
                # Rejected legacy objects must not make a failure table
                # unwritable. The source envelope stays untouched; explicitly
                # label this text representation of the rejected object.
                try:
                    failed["raw"] = json.dumps(failed["raw"], ensure_ascii=True, allow_nan=True)
                    failed["raw_representation"] = "ascii_escaped_json_text"
                except (ValueError, TypeError, RecursionError) as raw_error:
                    failed["raw"] = None
                    failed["raw_representation"] = "unavailable_unserializable_object"
                    failed["raw_serialization_error"] = str(raw_error)
            outcomes.append(failed)
            continue
        outcomes.append({**mention, "status": "completed"})
        mapping = {}
        units = {}
        declared_keys = {p["key"] for p in answer["columns"]
                         if isinstance(p, dict) and isinstance(p.get("key"), str)}
        for proposal in answer["columns"]:
            if (not isinstance(proposal, dict) or not isinstance(proposal.get("key"), str)
                    or not isinstance(proposal.get("meaning"), str) or not proposal["meaning"].strip()
                    or not isinstance(proposal.get("type"), str)
                    or proposal["type"] not in TYPES
                    or (proposal.get("unit") is not None and not isinstance(proposal["unit"], str))):
                table["issues"].append({**mention, "reason": "invalid_column", "proposal": proposal})
                continue
            raw_key = proposal["key"]
            if raw_key in mapping:
                table["issues"].append({**mention, "reason": "duplicate_column_key", "key": raw_key})
                continue
            existing = next((c for c in columns if signature(c) == signature(proposal)), None)
            if existing is None:
                if len(columns) - before[1] >= max_new_columns or len(columns) >= max_total_columns:
                    table["issues"].append({**mention, "reason": "column_budget", "key": raw_key})
                    continue
                base = re.sub(r"[^a-z0-9_]+", "_", raw_key.casefold()).strip("_") or "column"
                if base in RESERVED:
                    base = "column_" + base
                key, suffix = base, 2
                while any(c["key"] == key for c in columns):
                    key, suffix = f"{base}_{suffix}", suffix + 1
                existing = {"key": key, "label": str(proposal.get("label", raw_key)),
                            "meaning": proposal["meaning"], "type": proposal["type"],
                            "unit": proposal.get("unit"), "added_in_round": round_number, "proposed_by": []}
                columns.append(existing)
                for old in rows:
                    old["cells"][key] = cell()
            if mention not in existing["proposed_by"]:
                existing["proposed_by"].append(mention)
            mapping[raw_key] = existing["key"]
            units[raw_key] = proposal.get("unit")
        for candidate in answer["candidates"]:
            try:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str) or not candidate["name"].strip():
                    raise ValueError("candidate needs a name")
                name = candidate["name"].strip()
                url, host = normalize_url(candidate["homepage"])
                values = candidate.get("values", {})
                if not isinstance(values, dict):
                    raise ValueError("candidate values must be an object")
            except (ValueError, KeyError) as exc:
                table["issues"].append({**mention, "reason": "invalid_candidate", "detail": str(exc), "raw": candidate})
                continue
            identity = " ".join(name.casefold().split()) + "\n" + url
            candidate_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
            row = next((r for r in rows if r["candidate_id"] == candidate_id), None)
            if row is not None:
                if mention not in row["mentioned_by"]:
                    row["mentioned_by"].append(mention)
                row["observations"].append({**mention, "raw": candidate, "fetched_at": envelope.get("fetched_at")})
                continue
            if accepted_by_provider.get(provider, 0) >= max_new:
                table["issues"].append({**mention, "reason": "candidate_budget",
                                        "candidate_id": candidate_id, "raw": candidate})
                continue
            accepted_by_provider[provider] = accepted_by_provider.get(provider, 0) + 1
            ambiguous = [r["candidate_id"] for r in rows if r["lookup_host"] == host and r["cells"]["name"]["value"].casefold() == name.casefold()]
            if ambiguous:
                table["issues"].append({**mention, "reason": "ambiguous_identity", "candidate_id": candidate_id, "possible_matches": ambiguous})
            row = {"candidate_id": candidate_id, "lookup_host": host, "homepage_status": "unconfirmed",
                   "verification_status": "unverified", "mentioned_by": [mention],
                   "observations": [{**mention, "raw": candidate, "fetched_at": envelope.get("fetched_at")}],
                   "cells": {c["key"]: cell() for c in columns}}
            by_key = {c["key"]: c for c in columns}
            resolved = {}
            for raw_key, value in values.items():
                key = mapping.get(raw_key)
                if key is None and raw_key in accepted_keys and raw_key not in declared_keys:
                    key = raw_key
                if key is None or key in {"name", "homepage", "domain"}:
                    continue
                resolved.setdefault(key, []).append({"key": raw_key, "value": value,
                                                     "unit": units.get(raw_key, by_key[key]["unit"])})
            # Identity columns cannot be replaced by proposed values.
            for key, value in (("name", name), ("homepage", candidate["homepage"]), ("domain", host)):
                resolved[key] = [{"key": key, "value": value, "unit": by_key[key]["unit"]}]
            for key, inputs in resolved.items():
                c = by_key[key]
                first = inputs[0]
                conflict = any(item["value"] != first["value"] or item["unit"] != first["unit"]
                               or type_fits(item["value"], c["type"]) != type_fits(first["value"], c["type"])
                               for item in inputs[1:])
                row["cells"][key] = cell(None if conflict else first["value"], kind=c["type"],
                                         unit=first["unit"], source=source, fetched_at=envelope.get("fetched_at"))
                row["cells"][key]["method"] = method
                if conflict:
                    row["cells"][key]["reason"] = "conflicting_values"
                    table["issues"].append({**mention, "reason": "conflicting_values", "candidate_id": candidate_id,
                                            "column_key": key, "inputs": inputs})
            rows.append(row)
    progress = {"round": round_number, "new_candidates": len(rows) - before[0],
                "new_columns": len(columns) - before[1], "providers": outcomes}
    progress["stop"] = not (progress["new_candidates"] or progress["new_columns"])
    table["rounds"].append(progress)
    return table

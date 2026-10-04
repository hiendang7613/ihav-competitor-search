"""Conservative identity matching, typed columns and traceable cells."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

CORE = {
    "name": "text", "homepage": "url", "domain": "text", "category": "category",
    "description": "text", "pricing_model": "category", "free_tier": "bool",
    "open_source": "bool", "founded": "number", "headquarters": "text",
}
TYPES = {"number", "bool", "category", "text", "url"}
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
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
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


def parse_answer(raw):
    if isinstance(raw, dict):
        result = raw
    elif isinstance(raw, str):
        match = re.fullmatch(r"\s*```(?:json)?\s*\n?(.*?)\n?```\s*", raw, re.S)
        result = json.loads(match.group(1) if match else raw,
                            parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {v}")))
    else:
        raise ValueError("answer must be a JSON object or string")
    if not isinstance(result, dict) or not isinstance(result.get("columns"), list) or not isinstance(result.get("candidates"), list):
        raise ValueError("answer needs columns and candidates arrays")
    return result


def merge_round(table, answers, round_number, *, max_new_columns=5, max_total_columns=25):
    """Return a new table; answers carry provider, raw and optional fetched_at.

    No fuzzy/LLM matching or currency conversion is performed. Same name AND
    normalized product URL merges; possible aliases stay separate for host review.
    Repeated candidates add mentions/evidence but do not backfill old rows.
    """
    if (isinstance(max_new_columns, bool) or not isinstance(max_new_columns, int)
            or isinstance(max_total_columns, bool) or not isinstance(max_total_columns, int)
            or max_new_columns < 0 or max_total_columns < len(CORE)):
        raise ValueError("invalid column budgets")
    table = copy.deepcopy(table)
    columns = table["columns"]
    rows = table["rows"]
    before = len(rows), len(columns)
    outcomes = []
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
            outcomes.append({**mention, "status": "parse_failed", "raw": envelope["raw"], "reason": str(exc)})
            continue
        outcomes.append({**mention, "status": "completed"})
        mapping = {}
        units = {}
        for proposal in answer["columns"]:
            if (not isinstance(proposal, dict) or not isinstance(proposal.get("key"), str)
                    or not isinstance(proposal.get("meaning"), str) or not proposal["meaning"].strip()
                    or proposal.get("type") not in TYPES
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
            ambiguous = [r["candidate_id"] for r in rows if r["lookup_host"] == host and r["cells"]["name"]["value"].casefold() == name.casefold()]
            if ambiguous:
                table["issues"].append({**mention, "reason": "ambiguous_identity", "candidate_id": candidate_id, "possible_matches": ambiguous})
            row = {"candidate_id": candidate_id, "lookup_host": host, "homepage_status": "unconfirmed",
                   "verification_status": "unverified", "mentioned_by": [mention],
                   "observations": [{**mention, "raw": candidate, "fetched_at": envelope.get("fetched_at")}],
                   "cells": {c["key"]: cell() for c in columns}}
            data = {**{k: v for k, v in values.items() if k in CORE and k not in {"name", "homepage", "domain"}
                       and (k not in mapping or mapping[k] == k)},
                    "name": name, "homepage": candidate["homepage"], "domain": host}
            for raw_key, value in values.items():
                if raw_key in mapping:
                    data[mapping[raw_key]] = value
            # Identity columns cannot be replaced by proposed values.
            data.update(name=name, homepage=candidate["homepage"], domain=host)
            by_key = {c["key"]: c for c in columns}
            for key, value in data.items():
                c = by_key[key]
                raw_unit = next((units[k] for k in mapping if mapping[k] == key), c["unit"])
                row["cells"][key] = cell(value, kind=c["type"], unit=raw_unit, source=source, fetched_at=envelope.get("fetched_at"))
                row["cells"][key]["method"] = method
            rows.append(row)
    progress = {"round": round_number, "new_candidates": len(rows) - before[0],
                "new_columns": len(columns) - before[1], "providers": outcomes}
    progress["stop"] = not (progress["new_candidates"] or progress["new_columns"])
    table["rounds"].append(progress)
    return table

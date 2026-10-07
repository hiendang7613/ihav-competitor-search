"""Rank recorded counter responses; this module never performs a lookup."""
import copy
from collections import Counter

from .merge import MAX_JSON_DEPTH, validate_json_value


def apply_visits(table, visits):
    """visits maps normalized lookup_host to {exit_code, result, reason}.

    An absent entry is unqueried, not no-data. Explicit recorded results are
    evidence of prior lookups; they do not mark chatbot homepages verified.
    """
    if not isinstance(visits, dict):
        raise ValueError("visits must be an object keyed by lookup host")
    table = copy.deepcopy(table)
    counts = Counter(r["lookup_host"] for r in table["rows"])
    for row in table["rows"]:
        record = visits.get(row["lookup_host"])
        if row.get("homepage_decision_required") and row.get("homepage_status") != "confirmed":
            record = {"exit_code": None, "reason": "homepage_unconfirmed"}
        if record is not None and not isinstance(record, dict):
            raise ValueError("counter response must be an object")
        if record is not None and record.get("exit_code") is not None and (isinstance(record["exit_code"], bool) or not isinstance(record["exit_code"], int)):
            raise ValueError("counter exit_code must be an integer or null")
        if record is not None:
            # A response envelope adds one level around the child JSON.
            validate_json_value(record, max_depth=MAX_JSON_DEPTH + 1)
            if record.get("result") is not None and record.get("exit_code") != 0:
                raise ValueError("counter result requires successful exit_code 0")
        row["shared_domain"] = counts[row["lookup_host"]] > 1
        row["traffic"] = copy.deepcopy(record)
        row["traffic_rank"] = None
        if record is None or record.get("exit_code") is None:
            status, basis = "not_looked_up", None
        elif record["exit_code"] == 2:
            status, basis = "no_data", None
        elif record["exit_code"] in {4, 5, 64}:
            status, basis = "lookup_failed", None
        elif record["exit_code"] == 0:
            result = record.get("result", {})
            if not isinstance(result, dict):
                raise ValueError("counter result must be an object")
            if result.get("domain", row["lookup_host"]) != row["lookup_host"]:
                raise ValueError("counter result domain does not match lookup host")
            status = result.get("kind")
            if status == "estimate":
                value = result.get("monthly_visits")
                display = result.get("monthly_visits_text")
                if value is None and isinstance(display, str) and display.strip():
                    basis = None
                elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    basis = "monthly_visits"
                else:
                    raise ValueError("estimate requires nonnegative integer visits or a display-only estimate")
            elif status == "rank_only":
                rank = result.get("rank")
                value = rank.get("value") if isinstance(rank, dict) else None
                if (result.get("monthly_visits") is not None or result.get("monthly_visits_text") is not None
                        or isinstance(value, bool) or not isinstance(value, int) or value < 1):
                    raise ValueError("rank_only requires positive rank.value and null monthly_visits and monthly_visits_text")
                basis = "tranco_rank"
            else:
                raise ValueError("unsupported counter result kind")
        else:
            raise ValueError("unsupported counter exit_code")
        row["lookup_status"], row["rank_basis"] = status, basis
    groups = {"estimate": 0, "rank_only": 1, "no_data": 2, "lookup_failed": 3, "not_looked_up": 4}

    def key(row):
        status = row["lookup_status"]
        result = (row["traffic"] or {}).get("result") or {}
        display_only = status == "estimate" and result.get("monthly_visits") is None
        number = -result["monthly_visits"] if status == "estimate" and not display_only else result["rank"]["value"] if status == "rank_only" else 0
        return (groups[status], row["shared_domain"], display_only, number,
                row["cells"]["name"]["value"].casefold(), row["candidate_id"])

    table["rows"].sort(key=key)
    for position, row in enumerate(table["rows"], 1):
        row["display_position"] = position
        if row["rank_basis"] is not None:
            row["traffic_rank"] = position
    return table

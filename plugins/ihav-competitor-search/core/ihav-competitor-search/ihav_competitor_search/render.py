"""Standalone, escaped exports with a separate audit-friendly evidence CSV."""
import csv
import html
import io
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

META = ["candidate_id", "traffic_rank", "lookup_host", "monthly_visits", "tranco_rank",
        "lookup_status", "rank_basis", "shared_domain", "verification_status", "mentioned_by",
        "traffic_kind", "analyzed_at", "traffic_source", "lookup_reason", "homepage_status",
        "monthly_visits_text", "stale", "scraped_at"]
EVIDENCE = ["candidate_id", "column_key", "value", "raw_value", "raw_unit", "source_url",
            "fetched_at", "method", "verification", "reason"]


def text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def safe_link(value):
    return isinstance(value, str) and urlsplit(value).scheme.lower() in {"https", "http"}


def flat_row(row, columns):
    result = (row.get("traffic") or {}).get("result") or {}
    return {**{key: text(row.get(key)) for key in META},
            "monthly_visits": text(result.get("monthly_visits")),
            "tranco_rank": text((result.get("rank") or {}).get("value")),
            "traffic_kind": text(result.get("kind")), "analyzed_at": text(result.get("analyzed_at")),
            "traffic_source": text(result.get("source")), "lookup_reason": text((row.get("traffic") or {}).get("reason")),
            "monthly_visits_text": text(result.get("monthly_visits_text")),
            "stale": text(result.get("stale")), "scraped_at": text(result.get("scraped_at")),
            **{c["key"]: text(row["cells"][c["key"]]["value"]) for c in columns}}


def csv_text(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def atomic_write(path, content):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export(table, directory):
    directory = Path(directory)
    columns, rows = table["columns"], table["rows"]
    fields = META + [c["key"] for c in columns]
    flat = [flat_row(row, columns) for row in rows]
    evidence = [{"candidate_id": row["candidate_id"], "column_key": c["key"],
                 **{k: text(row["cells"][c["key"]].get(k)) for k in EVIDENCE[2:]}}
                for row in rows for c in columns]
    escape_md = lambda value: html.escape(text(value)).replace("|", "&#124;").replace("\n", "<br>").replace("\r", "")
    md = ["# Competitor survey", "", "Traffic is modelled, not owner analytics. This offline report has no live verification.", "",
          "| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    footnotes = []
    for row, values in zip(rows, flat):
        display = {k: escape_md(v) for k, v in values.items()}
        for c in columns:
            cell = row["cells"][c["key"]]
            if cell["verification"] == "verified" and safe_link(cell["source_url"]):
                n = len(footnotes) + 1
                display[c["key"]] += f"<sup>{n}</sup>"
                url = html.escape(cell["source_url"], quote=True)
                footnotes.append(f'{n}. <a href="{url}">{escape_md(cell["source_url"])}</a> — {escape_md(cell["fetched_at"])} — {escape_md(cell["method"])}')
        md.append("| " + " | ".join(display[k] for k in fields) + " |")
    md += ["", "Evidence: evidence.csv and table.json.", "", *footnotes]
    if table["issues"]:
        md += ["", "Merge issues:", *["- " + escape_md(json.dumps(issue, ensure_ascii=False)) for issue in table["issues"]]]
    headings = "".join(f"<th>{html.escape(k)}</th>" for k in fields)
    body = []
    for row, values in zip(rows, flat):
        cells = []
        for key in fields:
            value = html.escape(values[key])
            evidence_cell = row["cells"].get(key)
            if evidence_cell and evidence_cell["verification"] == "verified" and safe_link(evidence_cell["source_url"]):
                title = html.escape(f'{evidence_cell["fetched_at"]} · {evidence_cell["method"]}', quote=True)
                value = f'<a href="{html.escape(evidence_cell["source_url"], quote=True)}" title="{title}">{value}</a>'
            cells.append(f"<td>{value}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    summary = html.escape(json.dumps({"merge": table["rounds"], "chatbot": table.get("chatbot_rounds", {})}, ensure_ascii=False, indent=2))
    issues = html.escape(json.dumps(table["issues"], ensure_ascii=False, indent=2))
    report = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
              '<title>Competitor survey</title><style>body{font:15px system-ui;margin:2rem;color:#172033;background:#f5f7fb}'
              'table{border-collapse:collapse;background:white}td,th{border:1px solid #dde3ee;padding:.7rem;text-align:left}'
              '.table{overflow:auto}pre{white-space:pre-wrap}a{color:#1759ba}</style><h1>Competitor survey</h1>'
              '<p>Offline report. Traffic is modelled, not owner analytics. Homepages and cells have not been verified by this core.</p>'
              '<p>Full cell provenance: evidence.csv and table.json. Interactive charts are deferred.</p>'
              f'<div class="table"><table><thead><tr>{headings}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
              f'<details><summary>Recorded rounds and provider outcomes</summary><pre>{summary}</pre></details>'
              f'<details><summary>Merge issues and ambiguous matches</summary><pre>{issues}</pre></details></html>')
    outputs = {"table.json": json.dumps(table, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
               "table.csv": csv_text(fields, flat), "evidence.csv": csv_text(EVIDENCE, evidence),
               "table.md": "\n".join(md) + "\n", "report.html": report}
    for name, content in outputs.items():
        atomic_write(directory / name, content)
    return list(outputs)

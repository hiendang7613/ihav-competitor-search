"""Offline child CLI; intentionally imports no network modules."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("host")
parser.add_argument("--json", action="store_true")
parser.add_argument("--cache-dir", type=Path, required=True)
args = parser.parse_args()
with (Path.cwd() / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"host": args.host, "cwd": str(Path.cwd()), "cache": str(args.cache_dir)}) + "\n")
if args.host.startswith("blocked"):
    print(json.dumps({"error": {"message": "blocked"}}))
    raise SystemExit(4)
if args.host.startswith("missing"):
    print(json.dumps({"error": {"message": "no data"}}))
    raise SystemExit(2)
if args.host.startswith("failed"):
    print(json.dumps({"error": {"message": "network error (synthetic)"}}))
    raise SystemExit(5)
result = {"domain": args.host, "kind": "estimate", "monthly_visits": 100,
          "monthly_visits_text": "100", "source": {"name": "Synthetic fake"}, "analyzed_at": "2026-10-03"}
if args.host.startswith("ranked"):
    result.update(kind="rank_only", monthly_visits=None, monthly_visits_text=None, rank={"value": 123})
if args.host.startswith("display"):
    result.update(monthly_visits=None, monthly_visits_text="12.3K", stale=True, scraped_at="2026-09-01")
if args.host.startswith("fallback"):
    result["notes"] = ["Using this fallback after WebTrafficChecker failed: WebTrafficChecker returned HTTP 403; stopped this source without retrying.. No blocked source was retried."]
if args.host.startswith("v2-"):
    outcome = "blocked" if args.host.startswith("v2-block") else "cached" if args.host.startswith("v2-cached") else "ok"
    result.update(contract_version=2, providers=[{"name": "webtrafficchecker", "outcome": outcome,
        "http_status": 403 if outcome == "blocked" else 200, "detail": "Synthetic outcome"}])
    if outcome == "blocked":
        result["providers"].append({"name": "trafficlens", "outcome": "ok", "http_status": 200, "detail": None})
    if outcome == "cached":
        result.update(fetched_at="2026-10-03", notes=["WebTrafficChecker failed: HTTP 403; historical note"])
    if args.host.startswith("v2-block-error"):
        result = {"contract_version": 2, "providers": result["providers"][:1], "error": {"message": "synthetic block"}}
        print(json.dumps(result))
        raise SystemExit(4)
print(json.dumps(result))

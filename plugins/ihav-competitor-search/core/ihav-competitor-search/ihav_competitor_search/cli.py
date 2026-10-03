"""Read/render saved runs offline; lookup explicitly calls the counter dependency."""
import argparse
import json
import re
import sys
from pathlib import Path

from .merge import empty_table, merge_round, normalize_url
from .rank import apply_visits
from .render import atomic_write, export
from .visits import CounterVisitStage, lookup_hosts, source_blocked
from .homepages import HTTPHomepageStage, check_homepages, confirm_homepage, apply_homepage_decisions


def load(path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {v}")))


def run_path(project, run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("invalid run id")
    root = (project / ".ihav_space" / "ihav-competitor-search" / "runs").resolve()
    directory = root / run_id
    if not directory.is_dir() or directory.resolve().parent != root:
        raise ValueError("saved run does not exist or escapes the run root")
    return directory


def synthesize(directory):
    request = load(directory / "request.json")
    options = request.get("options", {})
    table = empty_table()
    rounds = sorted((directory / "rounds").iterdir(), key=lambda p: int(p.name))
    if any(not folder.is_dir() or not re.fullmatch(r"[1-9][0-9]*", folder.name) for folder in rounds):
        raise ValueError("round folders must be positive integers")
    limit = options.get("rounds", 2)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("rounds must be a positive integer")
    snapshots = []
    for folder in rounds[:limit]:
        answers = [load(p) for p in sorted((folder / "answers").glob("*.json"))]
        table = merge_round(table, answers, int(folder.name),
                            max_new_columns=options.get("max_new_columns", 5),
                            max_total_columns=options.get("max_total_columns", 25))
        snapshots.append((folder.name, json.dumps(table, ensure_ascii=False, indent=2, allow_nan=False) + "\n"))
        if table["rounds"][-1]["stop"]:
            break
    table = apply_homepage_decisions(table, request, directory)
    visits = load(directory / "visits.json") if (directory / "visits.json").exists() else {}
    table = apply_visits(table, visits)
    table["request"] = request
    table["stages"] = {"ask": "recorded_input", "merge": "completed", "rank": "recorded_input",
                       "homepage_check": "recorded_input", "verification": "not_implemented"}
    return table, snapshots


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="calling project; defaults to cwd")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "render"):
        sub.add_parser(command).add_argument("run_id")
    lookup = sub.add_parser("lookup", help="call the counter for confirmed hosts; may access the network")
    lookup.add_argument("run_id")
    lookup.add_argument("--counter", type=Path, help="path to installed counter scripts/visits.py")
    lookup.add_argument("--max-lookups", type=int, default=100)
    check = sub.add_parser("check", help="one bounded homepage GET per unconfirmed candidate; may access network")
    check.add_argument("run_id")
    confirm = sub.add_parser("confirm", help="record host-agent product confirmation; no network")
    confirm.add_argument("run_id")
    confirm.add_argument("candidate_id")
    choice = confirm.add_mutually_exclusive_group(required=True)
    choice.add_argument("--url")
    choice.add_argument("--unconfirmed", help="reason official homepage remains unconfirmed")
    confirm.add_argument("--method", choices=["page", "official_search"], default="page")
    args = parser.parse_args(argv)
    try:
        directory = run_path(args.project, args.run_id)
        if args.command == "status":
            request = load(directory / "request.json")
            visits = load(directory / "visits.json") if (directory / "visits.json").exists() else {}
            print(json.dumps({"run_id": args.run_id, "request": request,
                              "rendered": (directory / "table.json").exists(),
                              "lookup": {"recorded_hosts": len(visits),
                                         "blocked": any(source_blocked(v) for v in visits.values()),
                                         "unknown": any(v.get("state") in {"launching", "unknown"} for v in visits.values())},
                              "stages": {"chatbot": "not_implemented", "homepage": "host_confirmation", "verification": "not_implemented"}}))
        elif args.command == "check":
            table, _ = synthesize(directory)
            checks = check_homepages(table, directory, HTTPHomepageStage())
            print(json.dumps({"run_id": args.run_id, "checks": checks}))
        elif args.command == "confirm":
            table, _ = synthesize(directory)
            record = confirm_homepage(table, directory, args.candidate_id, args.url,
                                      reason=args.unconfirmed, method=args.method)
            print(json.dumps(record))
        elif args.command == "lookup":
            stage = CounterVisitStage(args.counter, args.project)
            table, _ = synthesize(directory)
            # Use recorded host-agent confirmation evidence, never a
            # model-generated homepage alone.
            confirmations = table["request"].get("homepage_confirmations", {})
            if not isinstance(confirmations, dict):
                raise ValueError("homepage_confirmations must be a host-keyed object")
            eligible = set()
            for host, evidence in confirmations.items():
                if not isinstance(evidence, dict) or evidence.get("status") != "confirmed":
                    continue
                if not isinstance(evidence.get("fetched_at"), str) or not evidence["fetched_at"].strip():
                    continue
                try:
                    _, source_host = normalize_url(evidence.get("source_url"))
                except ValueError:
                    continue
                if source_host == host:
                    eligible.add(host)
            if any(row.get("homepage_decision_required") for row in table["rows"]):
                eligible &= {row["lookup_host"] for row in table["rows"] if row["homepage_status"] == "confirmed"}
            visits = lookup_hosts(table, directory, stage, max_lookups=args.max_lookups,
                                  eligible_hosts=eligible)
            print(json.dumps({"run_id": args.run_id, "hosts": len(visits),
                              "attempted": sum(v.get("exit_code") is not None for v in visits.values()),
                              "blocked": any(source_blocked(v) for v in visits.values()),
                              "unknown": any(v.get("state") == "unknown" for v in visits.values())}))
        else:
            ignore = args.project / ".gitignore"
            if not ignore.exists() or ".ihav_space/" not in ignore.read_text().splitlines():
                print("Warning: add .ihav_space/ to the calling project's .gitignore.", file=sys.stderr)
            table, snapshots = synthesize(directory)
            outputs = export(table, directory)
            synthesis = directory / "synthesis"
            synthesis.mkdir(exist_ok=True)
            for name, content in snapshots:
                atomic_write(synthesis / f"{name}.json", content)
            print(json.dumps({"run_id": args.run_id, "rows": len(table["rows"]), "outputs": outputs,
                              "verification": "not_implemented"}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Read/render saved runs offline; lookup explicitly calls the counter dependency."""
import argparse
import json
import sys
import subprocess
from pathlib import Path

from .merge import normalize_url
from .render import atomic_write, export
from .visits import CounterVisitStage, lookup_hosts, source_blocked
from .homepages import HTTPHomepageStage, check_homepages, confirm_homepage
from .chatbots import WebChatChild, prepare, ask, collect, preview
from .manual import prompt, import_answer, validate_round
from .verification import load_evidence, record_cell_evidence
from .survey import create_run, make_request, progress, run_root, safe_id
from .synthesis import load, synthesize
from . import research


def run_path(project, run_id):
    safe_id(run_id)
    root = run_root(project)
    directory = root / run_id
    if not directory.is_dir() or directory.resolve() != directory:
        raise ValueError("saved run does not exist or escapes the run root")
    if (directory / "request.json").is_symlink():
        raise ValueError("saved request escapes the run")
    return directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="calling project; defaults to cwd")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "survey"):
        start = sub.add_parser(command, help="create a saved run; survey advances one child queue or collection step")
        start.add_argument("domain", nargs="?" if command == "survey" else None)
        start.add_argument("--run-id")
        selection = start.add_mutually_exclusive_group()
        selection.add_argument("--providers", default=None, help="comma-separated names, or all")
        selection.add_argument("--research-targets-file", type=Path,
                               help="versioned exact research descriptors for at least two providers")
        start.add_argument("--rounds", type=int, default=2)
        start.add_argument("--max-new", type=int, default=30)
        start.add_argument("--max-new-columns", type=int, default=5)
        start.add_argument("--max-total-columns", type=int, default=25)
        start.add_argument("--max-lookups", type=int, default=100)
        start.add_argument("--verify-top", type=int, default=50)
        start.add_argument("--no-verify", action="store_true")
        if command == "survey":
            start.add_argument("--web-chat", type=Path)
            start.add_argument("--dry-run", action="store_true")
            start.add_argument("--opt-in", action="store_true")
    verify = sub.add_parser("verify", help="record checked official-page evidence from a local JSON file; no network")
    verify.add_argument("run_id")
    verify.add_argument("--file", type=Path, required=True)
    verify.add_argument("--replace", action="store_true")
    for command in ("status", "render"):
        sub.add_parser(command).add_argument("run_id")
    lookup = sub.add_parser("lookup", help="call the counter for confirmed hosts; may access the network")
    lookup.add_argument("run_id")
    lookup.add_argument("--counter", type=Path, help="path to installed counter scripts/visits.py")
    lookup.add_argument("--max-lookups", type=int, default=None, help="explicitly set this run's cap; default saved cap or 100")
    check = sub.add_parser("check", help="one bounded homepage GET per unconfirmed candidate; may access network")
    check.add_argument("run_id")
    confirm = sub.add_parser("confirm", help="record host-agent product confirmation; no network")
    confirm.add_argument("run_id")
    confirm.add_argument("candidate_id")
    choice = confirm.add_mutually_exclusive_group(required=True)
    choice.add_argument("--url")
    choice.add_argument("--unconfirmed", help="reason official homepage remains unconfirmed")
    confirm.add_argument("--method", choices=["page", "official_search"], default="page")
    manual_prompt = sub.add_parser("prompt", help="print a copyable prompt; no chatbot call or opt-in")
    manual_prompt.add_argument("run_id")
    manual_prompt.add_argument("--round", type=int, default=1)
    answer = sub.add_parser("answer", help="store pasted JSON locally; no chatbot call")
    answer.add_argument("run_id")
    answer.add_argument("--round", type=int, required=True)
    answer.add_argument("--provider", required=True)
    answer.add_argument("--file", default="-", help="UTF-8 answer file; - or omitted reads stdin")
    answer.add_argument("--replace", action="store_true")
    for command in ("ask", "resume", "collect"):
        chatbot = sub.add_parser(command, help="gated child queue, exact recovery or inline delivery ingestion")
        chatbot.add_argument("run_id")
        chatbot.add_argument("--web-chat", type=Path, help="installed child CLI; or IHAV_WEB_CHAT")
        chatbot.add_argument("--round", type=int, default=1)
        if command != "collect":
            chatbot.add_argument("--dry-run", action="store_true")
            chatbot.add_argument("--opt-in", action="store_true", help="explicit consent after reviewing the preview")
    args = parser.parse_args(argv)
    try:
        if args.command in {"init", "survey"}:
            if args.command == "survey" and args.domain is None:
                if not args.run_id:
                    raise ValueError("survey needs domain text or --run-id for an existing run")
                if args.research_targets_file is not None:
                    raise ValueError("saved research targets are immutable; create a new run to change them")
                directory = run_path(args.project, args.run_id)
            else:
                selection = "all" if args.providers == "all" else args.providers.split(",") if args.providers is not None else None
                targets = None
                if args.research_targets_file is not None:
                    if args.research_targets_file.stat().st_size > research.MAX_METADATA_BYTES:
                        raise ValueError("research target file exceeds 1 MiB")
                    targets = research.target_set(load(args.research_targets_file), research_only=True)
                if args.command == "survey":
                    from_request = make_request(args.domain, providers=selection, targets=targets, rounds=args.rounds,
                                                max_new=args.max_new, max_new_columns=args.max_new_columns,
                                                max_total_columns=args.max_total_columns, max_lookups=args.max_lookups,
                                                verify_top=args.verify_top, no_verify=args.no_verify)
                    child = WebChatChild(args.web_chat, args.project)
                    caps = child.gate(dispatch=args.opt_in and not args.dry_run)
                    outbound = preview(from_request, caps["providers"])
                    if targets is not None:
                        research.require_capabilities(caps, targets)
                        outbound.update(targets=targets, round_budget=research.budgets(from_request),
                                        authorization_basis="persisted_trusted_host_scope_consistency")
                    if args.dry_run or not args.opt_in:
                        print(json.dumps({"preview": outbound, "dry_run": args.dry_run,
                                          "state": "preview" if args.dry_run else "consent_required"}))
                        return 0 if args.dry_run else 2
                directory = create_run(args.project, args.domain, run_id=args.run_id, providers=selection, targets=targets,
                                       rounds=args.rounds, max_new=args.max_new, max_new_columns=args.max_new_columns,
                                       max_total_columns=args.max_total_columns, max_lookups=args.max_lookups,
                                       verify_top=args.verify_top, no_verify=args.no_verify)
            args.run_id = directory.name
            if args.command == "init":
                print(json.dumps({"run_id": args.run_id, "state": "created", "next": "ask" if targets else "prompt"}))
                return 0
            table, _ = synthesize(directory)
            next_step = progress(directory, table)
            if next_step["action"] in {"ask", "collect"}:
                child = WebChatChild(args.web_chat, args.project)
                number = next_step["round"]
                prior = synthesize(directory, through_round=number - 1)[0] if number > 1 else None
                if next_step["action"] == "ask":
                    _, _, outbound = prepare(directory, child, number, table=prior)
                    print(json.dumps({"preview": outbound, "dry_run": args.dry_run}), flush=True)
                    if args.dry_run:
                        return 0
                    ask(directory, child, number, opt_in=args.opt_in, table=prior)
                    next_step = {"state": "awaiting_child", "action": "collect", "round": number}
                elif not args.dry_run:
                    ask(directory, child, number, table=prior)
                    collect(directory, child, number)
                    table, _ = synthesize(directory)
                    next_step = progress(directory, table)
            print(json.dumps({"run_id": args.run_id, **next_step}))
            return 0
        directory = run_path(args.project, args.run_id)
        if args.command == "prompt":
            validate_round(directory, args.round)
            table = synthesize(directory, through_round=args.round - 1)[0] if args.round > 1 else None
            if args.round > 1 and not table["rounds"]:
                raise ValueError("later rounds require prior recorded answers")
            print(prompt(directory, args.round, table), end="")
        elif args.command == "answer":
            if args.file == "-":
                raw = sys.stdin.read()
            else:
                with Path(args.file).open(encoding="utf-8", newline="") as handle:
                    raw = handle.read()
            record = import_answer(directory, args.round, args.provider, raw, replace=args.replace)
            print(json.dumps(record, ensure_ascii=False))
            return 2 if record["status"] == "parse_failed" else 0
        elif args.command in {"ask", "resume", "collect"}:
            prior = synthesize(directory, through_round=args.round - 1)[0] if args.command != "collect" and args.round > 1 else None
            child = WebChatChild(args.web_chat, args.project)
            if args.command == "collect":
                result = collect(directory, child, args.round)
            else:
                _, _, outbound = prepare(directory, child, args.round, table=prior)
                print(json.dumps({"preview": outbound, "dry_run": args.dry_run}), flush=True)
                if args.dry_run:
                    return 0
                result = ask(directory, child, args.round, opt_in=args.opt_in, table=prior)
            print(json.dumps(result))
        elif args.command == "verify":
            table, _ = synthesize(directory)
            record = record_cell_evidence(table, directory, load_evidence(args.file), replace=args.replace,
                                          top=table["request"].get("options", {}).get("verify_top", 50))
            print(json.dumps(record, ensure_ascii=False))
        elif args.command == "status":
            request = load(directory / "request.json")
            visits = load(directory / "visits.json") if (directory / "visits.json").exists() else {}
            table, _ = synthesize(directory)
            print(json.dumps({"run_id": args.run_id, "request": request,
                              "rounds": table["rounds"],
                              "mentioned_by": {row["candidate_id"]: row["mentioned_by"] for row in table["rows"]},
                              "rendered": (directory / "table.json").exists(),
                              "lookup": {"recorded_hosts": len(visits),
                                         "blocked": any(source_blocked(v) for v in visits.values()),
                                         "unknown": any(v.get("state") in {"launching", "unknown"} for v in visits.values())},
                              "survey": progress(directory, table), "verification": table["verification"],
                              "stages": {"chatbot": "capability_gated", "homepage": "host_confirmation", "verification": "host_supplied_page_evidence"}}))
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
            cap = args.max_lookups if args.max_lookups is not None else table["request"].get("options", {}).get("max_lookups", 100)
            if type(cap) is not int or cap < 0:
                raise ValueError("max-lookups must be a nonnegative integer")
            if args.max_lookups is not None:
                request = table["request"]
                request.setdefault("options", {})["max_lookups"] = cap
                atomic_write(directory / "request.json", json.dumps(request, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
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
            visits = lookup_hosts(table, directory, stage, max_lookups=cap,
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
            synthesis = directory / "synthesis"
            if synthesis.resolve() != synthesis or (synthesis.exists() and not synthesis.is_dir()):
                raise ValueError("synthesis output directory escapes the run or is not a directory")
            outputs = export(table, directory)
            synthesis.mkdir(exist_ok=True)
            for name, content in snapshots:
                atomic_write(synthesis / f"{name}.json", content)
            print(json.dumps({"run_id": args.run_id, "rows": len(table["rows"]), "outputs": outputs,
                              "verification": table["verification"], "survey": progress(directory, table)}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

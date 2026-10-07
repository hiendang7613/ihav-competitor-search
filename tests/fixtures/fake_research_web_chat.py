"""Explicitly synthetic typed-wire fixture; never imports a provider or worker.

The digest oracle is independent of the parent implementation. Every write is
under the pytest project, and a queue effect here is only a synthetic JSON log.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

project = Path.cwd()
config_path = project / "synthetic-chat-config.json"
config = json.loads(config_path.read_text()) if config_path.exists() else {}
args = sys.argv[1:]
stamp = "2026-10-07T07:00:00+00:00"
providers = ["synthetic_alpha", "synthetic_beta"]
targets = [{"provider": p, "kind": "research", "mode_id": "synthetic-research-v1",
            "settings": {"depth": "synthetic", "strict": True}} for p in providers]
with (project / "synthetic-calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def raw_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def option(name):
    return args[args.index(name) + 1]


def path(run_id):
    return project / ".ihav_space/ihav-web-chat/runs" / run_id / "request.json"


def load_request(run_id):
    return json.loads(path(run_id).read_text(encoding="utf-8"))


def target_identities(prompt, selected):
    return [{"provider": item["provider"], "outbound_sha256": sha({"prompt": prompt, **item})} for item in selected]


def statuses(request):
    states = config.get("statuses", {p: "completed" for p in request["providers"]})
    return {p: {"status": state, "reason": None, "updated_at": stamp} for p, state in states.items()}


if args[0] == "--help":
    print("run status delivery doctor")
elif args[0] == "doctor":
    catalog = {item["provider"]: [item] for item in targets}
    print(json.dumps({"version": "synthetic-target-wire-v2", "providers": providers,
                      "commands": ["run", "run lookup", "status", "delivery read", "delivery wait"],
                      "contracts": config.get("contracts", {"run_lookup_admission": 1, "launch_outcome": 1,
                                                           "target_execution": 1, "target_authorization": 1}),
                      "targets": config.get("catalog", catalog), "default_state_dir": str(project / "synthetic-state")}))
elif args[:2] == ["run", "lookup"]:
    key = option("--request-key")
    ids = [p.parent.name for p in (project / ".ihav_space/ihav-web-chat/runs").glob("*/request.json")
           if json.loads(p.read_text())["request_key"] == key]
    records = []
    for run_id in ids:
        request = load_request(run_id)
        identities = target_identities(request["prompt"], request["targets"])
        entries = [{"name": t["provider"], "job_id": run_id + "/" + t["provider"],
                    "prompt_sha256": raw_sha(request["prompt"]), "target": t,
                    "outbound_sha256": identities[index]["outbound_sha256"]}
                   for index, t in enumerate(request["targets"])]
        if config.get("admission_target_override"):
            entries[0]["target"] = config["admission_target_override"]
        record = {"run_id": run_id, "admission": config.get("admission", "admitted"),
                  "request_schema_version": 2, "outbound_scope_sha256": request["outbound_scope_sha256"],
                  "authorization_sha256": sha(request["authorization"]), "providers": entries}
        record.update(config.get("admission_overrides", {}))
        records.append(record)
    print(json.dumps({"request_key": key, "run_ids": config.get("lookup_ids", ids), "admission_schema_version": 1,
                      "state_dir": option("--state-dir"), "runs": records}))
elif args[0] == "run":
    assert "--providers" not in args, "typed target selection must be explicit"
    target_set = json.loads(Path(option("--targets-file")).read_text())
    auth = json.loads(Path(option("--authorization-file")).read_text())
    prompt_file = Path(option("--prompt-file"))
    prompt = prompt_file.read_bytes().decode("utf-8")
    selected = target_set["targets"]
    assert target_set["schema_version"] == 1
    key = option("--request-key")
    scope = {"effect": "web_chat_send", "request_key": key, "prompt_sha256": raw_sha(prompt),
             "targets": target_identities(prompt, selected)}
    caller = ({"host": "claude_code", "session": os.environ["CLAUDE_CODE_SESSION_ID"]}
              if os.environ.get("CLAUDE_CODE_SESSION_ID") else {"host": "codex", "session": os.environ["CODEX_THREAD_ID"]})
    expected = {"schema_version": 1, **scope, "outbound_scope_sha256": sha(scope),
                "issuer": caller, "issued_at": auth["issued_at"]}
    assert canonical(auth) == canonical(expected), "authorization must bind exact scope and trusted issuer"
    parent = json.loads((prompt_file.parents[2] / "request.json").read_text())
    assert canonical(parent["outbound_consent"]["targets"]) == canonical(selected)
    effects = project / "synthetic-queues.jsonl"
    number = len(effects.read_text().splitlines()) + 1 if effects.exists() else 1
    run_id = f"20261007T070000-{number:08x}"
    request = {"schema_version": 2, "run_id": run_id, "prompt": prompt, "providers": [t["provider"] for t in selected],
               "targets": selected, "outbound_scope_sha256": sha(scope), "authorization": auth,
               "caller": caller, "request_key": key, "options": {}, "created_at": stamp}
    request.update(config.get("request_overrides", {}))
    path(run_id).parent.mkdir(parents=True, exist_ok=True)
    path(run_id).write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    with effects.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"run_id": run_id, "request_key": key, "targets": selected,
                              "prompt": prompt, "round": int(prompt_file.parent.name)}) + "\n")
    print("lost reply" if config.get("lost_response") else json.dumps({"run_id": run_id, "state_dir": option("--state-dir")}))
elif args[0] == "status":
    request = load_request(args[1])
    print(json.dumps({"run_id": args[1], "providers": statuses(request)}))
elif args[:2] == ["delivery", "read"]:
    run_id = args[2]
    request = load_request(run_id)
    entries = statuses(request)
    parent_round = int(request["request_key"].split(":")[-3])
    identities = {item["provider"]: item["outbound_sha256"] for item in target_identities(request["prompt"], request["targets"])}
    for item in request["targets"]:
        p = item["provider"]
        if p not in entries or entries[p]["status"] != "completed":
            continue
        answer = {"columns": [], "candidates": [{"name": f"Synthetic {p} round {parent_round}",
                  "homepage": f"https://{p.replace('_', '-')}-{parent_round}.example", "values": {}}]}
        raw = config.get("answers", {}).get(p, "\r\n```json\r\n" + json.dumps(answer, ensure_ascii=False) + "\r\n```\r\n")
        receipt = {"schema_version": 1, "qualification": "qualified", "reason": None, "run_id": run_id,
                   "job_id": run_id + "/" + p, "provider": p, "prompt_sha256": raw_sha(request["prompt"]),
                   "outbound_sha256": identities[p], "requested": item, "observed": item,
                   "observation_status": "matched", "turn_binding": {"conversation_url": "https://synthetic.invalid/" + run_id,
                   "turn": 1, "marker": run_id + "/" + p}, "transport_status": "completed", "raw_answer_sha256": raw_sha(raw)}
        receipt.update(config.get("receipt_overrides", {}).get(p, {}))
        entries[p].update(response_text=raw, response_path="/never/read/synthetic-response.md")
        if p not in config.get("missing_receipts", []):
            entries[p]["execution_receipt"] = receipt
    for p in config.get("delivery_drop", []):
        entries.pop(p, None)
    print(json.dumps({"run_id": run_id, "providers": entries, "status": "completed", "all_terminal": True, "updated_at": stamp}, ensure_ascii=False))
else:
    raise SystemExit(2)

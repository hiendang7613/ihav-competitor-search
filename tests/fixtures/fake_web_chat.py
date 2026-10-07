"""Released CLI contract fixture; no worker, browser or provider implementation."""
import json
import hashlib
import sys
from pathlib import Path

project = Path.cwd()
config_path = project / "fake-chat-config.json"
config = json.loads(config_path.read_text()) if config_path.exists() else {}
args = sys.argv[1:]
command = args[0]
with (project / "chat-calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")


def request_path(run_id):
    return project / ".ihav_space/ihav-web-chat/runs" / run_id / "request.json"


def statuses(run_id):
    request = json.loads(request_path(run_id).read_text())
    configured = config.get("statuses", {p: {"status": "completed"} for p in request["providers"]})
    return {p: {"reason": None, "updated_at": "2026-10-06T00:00:00+00:00", **v}
            for p, v in configured.items()}


if command == "--help":
    print("run status providers" if config.get("missing_delivery") else "run status providers delivery doctor")
elif command == "doctor":
    print(json.dumps({"version": "fake-released-1", "commands": config.get("commands", ["run", "run lookup", "status", "delivery wait", "delivery read"]),
                      "providers": config.get("providers", ["chatgpt", "gemini", "perplexity"]),
                      "default_state_dir": config.get("state_dir", str(project / "fake-webchat-state")),
                      "contracts": config.get("contracts", {"run_lookup_admission": 1, "launch_outcome": 1})}))
elif command == "run" and len(args) > 1 and args[1] == "lookup":
    if config.get("lookup_failure"):
        raise SystemExit(3)
    key = args[args.index("--request-key") + 1]
    state_dir = args[args.index("--state-dir") + 1] if "--state-dir" in args else config.get("state_dir", str(project / "fake-webchat-state"))
    ids = [path.parent.name for path in (project / ".ihav_space/ihav-web-chat/runs").glob("*/request.json")
           if json.loads(path.read_text()).get("request_key") == key]
    records = []
    for run_id in ids:
        request = json.loads(request_path(run_id).read_text())
        digest = hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest()
        records.append({"run_id": run_id, "admission": config.get("admission", "admitted"),
                        "providers": [{"name": p, "job_id": run_id + "/" + p,
                                       "prompt_sha256": config.get("admission_digest", digest)}
                                      for p in request["providers"]]})
    print(json.dumps({"request_key": config.get("lookup_key", key), "run_ids": config.get("lookup_ids", ids),
                      "admission_schema_version": config.get("admission_schema_version", 1),
                      "state_dir": state_dir, "runs": records}))
elif command == "run":
    if config.get("refusal"):
        print(json.dumps(config["refusal"]))
        raise SystemExit(2)
    prompt = Path(args[args.index("--prompt-file") + 1])
    prompt_text = prompt.read_bytes().decode("utf-8")
    parent = json.loads((prompt.parents[2] / "request.json").read_text())
    assert parent["outbound_consent"]["recorded_at"]
    sends = project / "chat-sends.jsonl"
    number = len(sends.read_text().splitlines()) + 1 if sends.exists() else 1
    run_id = f"20261006T000000-{number:08x}"
    key = args[args.index("--request-key") + 1]
    state_dir = args[args.index("--state-dir") + 1] if "--state-dir" in args else config.get("state_dir", str(project / "fake-webchat-state"))
    providers = args[args.index("--providers") + 1].split(",")
    request = {"schema_version": 1, "run_id": run_id, "prompt": prompt_text, "providers": providers,
               "request_key": key, "created_at": "2026-10-06T00:00:00+00:00", "options": {}, "caller": None}
    request.update(config.get("request_overrides", {}))
    request_path(run_id).parent.mkdir(parents=True, exist_ok=True)
    request_path(run_id).write_text(json.dumps(request))
    with sends.open("a") as log:
        log.write(json.dumps({"prompt": prompt_text, "consent": parent["outbound_consent"],
                              "request_key": key, "run_id": run_id}) + "\n")
    if config.get("lost_response"):
        print("not json")
    else:
        print(json.dumps({"run_id": config.get("returned_id", run_id),
                          "state_dir": config.get("returned_state_dir", state_dir)}))
elif command == "status":
    print(json.dumps({"run_id": config.get("status_run_id", args[1]), "providers": statuses(args[1])}))
elif command == "delivery" and args[1] == "read":
    if config.get("delivery_failure"):
        raise SystemExit(3)
    run_id = args[2]
    entries = statuses(run_id)
    default = '```json\n' + json.dumps({"columns": [], "candidates": [{"name": "Fixture product", "homepage": "https://example.com", "values": {}}]}) + '\n```\n'
    for provider, entry in entries.items():
        if entry["status"] == "completed":
            entry.update(response_text=config.get("answers", {}).get(provider, default),
                         response_path=config.get("response_path", "/untrusted/response.md"),
                         sources_path="/untrusted/sources.json")
    print(json.dumps({"run_id": config.get("delivery_run_id", run_id), "providers": entries,
                      "updated_at": "2026-10-06T00:00:00+00:00", "all_terminal": True, "status": "completed"}))
else:
    raise SystemExit(2)

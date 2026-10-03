"""Proposed capability/status shape only; imports no network or provider code."""
import json
import sys
from pathlib import Path

project = Path.cwd()
config_path = project / "fake-chat-config.json"
config = json.loads(config_path.read_text()) if config_path.exists() else {}
args = sys.argv[1:]
command = args[0]
with (project / "chat-calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")
if command == "--help":
    print("run status providers" if config.get("missing_delivery") else "run status providers delivery doctor")
elif command == "doctor":
    print(json.dumps({"version": "fake-proposed-1", "commands": config.get("commands", ["run", "status", "delivery wait", "delivery read"]),
                      "providers": ["chatgpt", "gemini", "perplexity"]}))
elif command == "run":
    prompt = Path(args[args.index("--prompt-file") + 1])
    request = json.loads((prompt.parents[2] / "request.json").read_text())
    assert request["outbound_consent"]["recorded_at"]
    with (project / "chat-sends.jsonl").open("a") as log:
        log.write(json.dumps({"prompt": prompt.read_text(), "consent": request["outbound_consent"]}) + "\n")
    if config.get("lost_response"):
        print("not json")
    else:
        print(json.dumps({"run_id": "fake-child-1"}))
elif command == "status":
    print(json.dumps({"providers": config.get("statuses", {p: {"status": "completed"} for p in ["chatgpt", "gemini", "perplexity"]})}))
else:
    raise SystemExit(2)

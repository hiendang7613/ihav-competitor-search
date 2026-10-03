"""Gated ask-stage preparation; the child capability contract is still proposed."""
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .homepages import now
from .merge import CORE
from .render import atomic_write

TERMINAL = {"completed", "failed", "timeout", "login_required",
            "human_verification_required", "not_sent", "sent_unknown", "cancelled"}
FIELDS = ["domain verbatim", "candidate name", "candidate homepage", "accepted column values"]
INSTALL = "claude plugin install ihav-web-chat@ihav; then restart the host and rerun the gate. For Codex, install ihav-web-chat from the ihav marketplace and restart the host."


def save(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def ask_lock(directory):
    lock = directory / "ask.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("ask.lock exists; reconcile the prior writer before removing it") from None
    try:
        yield
    finally:
        lock.rmdir()


class WebChatChild:
    def __init__(self, explicit, project):
        value = explicit or os.environ.get("IHAV_WEB_CHAT")
        self.path = Path(value).expanduser().resolve() if value else None
        if self.path is None or not self.path.is_file():
            raise ValueError("ihav-web-chat CLI not found; set --web-chat or IHAV_WEB_CHAT. " + INSTALL)
        self.project = Path(project).resolve()

    def call(self, *args):
        command = ([sys.executable, str(self.path)] if self.path.suffix == ".py" else [str(self.path)]) + list(args)
        result = subprocess.run(command, cwd=self.project, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode:
            raise ValueError(f"ihav-web-chat {' '.join(args[:2])} failed with exit {result.returncode}")
        return result.stdout

    def gate(self):
        # Help is local discovery only. Never probe a missing command by launching work.
        help_text = self.call("--help")
        missing = [name for name in ("run", "status", "delivery")
                   if not re.search(r"\b" + name + r"\b", help_text)]
        if missing:
            commands = [command for name in missing for command in
                        (["delivery wait", "delivery read"] if name == "delivery" else [name])]
            raise ValueError("ihav-web-chat missing commands: " + ", ".join(commands) + ". " + INSTALL)
        if not re.search(r"\bdoctor\b", help_text):
            raise ValueError("ihav-web-chat missing versioned capabilities: doctor --json. " + INSTALL)
        caps = json.loads(self.call("doctor", "--json"))
        if not isinstance(caps, dict) or not isinstance(caps.get("version"), str) or not caps["version"]:
            raise ValueError("ihav-web-chat capability response requires version")
        commands = caps.get("commands")
        required = {"run", "status", "delivery wait", "delivery read"}
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            raise ValueError("ihav-web-chat capabilities.commands must be a list of command names")
        missing = sorted(required - set(commands))
        if missing:
            raise ValueError("ihav-web-chat missing commands: " + ", ".join(missing) + ". " + INSTALL)
        providers = caps.get("providers")
        if not isinstance(providers, list) or not providers or not all(
                isinstance(p, str) and re.fullmatch(r"[a-z][a-z0-9_-]*", p) for p in providers):
            raise ValueError("ihav-web-chat capabilities require a nonempty supported provider list")
        return caps

    def launch(self, providers, prompt_file):
        return json.loads(self.call("run", "--providers", ",".join(providers), "--prompt-file",
                                    str(prompt_file), "--project", str(self.project), "--json"))

    def status(self, child_id):
        return json.loads(self.call("status", child_id, "--project", str(self.project), "--json"))


def preview(request, supported, round_number=1, table=None):
    selection = request.get("providers", ["chatgpt", "gemini", "perplexity"])
    providers = list(supported) if selection == "all" else selection
    if not isinstance(providers, list) or not providers or len(set(providers)) != len(providers):
        raise ValueError("providers must be a nonempty unique list or all")
    if any(p not in supported for p in providers):
        raise ValueError("selected provider is not supported by the installed child")
    options = request.get("options", {})
    columns = list(CORE)
    prompt = ("Survey products or services in this domain (verbatim user text follows):\n"
              + request["domain"] + "\nReturn only one fenced JSON object with columns and candidates.\n"
              + "Each candidate has name, homepage and values; each column has key, label, type, unit and meaning.\n"
              + f"Return up to {options.get('max_new', 30)} candidates and propose up to {options.get('max_new_columns', 5)} extra columns.\n"
              + "Core columns: " + ", ".join(columns) + ".\n")
    if round_number > 1:
        if table is None:
            raise ValueError("later rounds require saved synthesis")
        # Explicit projection excludes raw observations, request notes and provenance.
        accepted = [c["key"] for c in table["columns"]]
        rows = [{"name": r["cells"]["name"]["value"], "homepage": r["cells"]["homepage"]["value"],
                 "values": {k: r["cells"][k]["value"] for k in accepted if k not in {"name", "homepage"}}}
                for r in table["rows"]]
        prompt += "Return only new candidates absent from this table; propose only new columns.\n"
        prompt += json.dumps({"columns": accepted, "candidates": rows}, ensure_ascii=False, allow_nan=False) + "\n"
    return {"providers": providers, "prompt": prompt, "outbound_field_kinds": FIELDS,
            "domain_sent_verbatim": True}


def prepare(directory, child, round_number):
    request = read(directory / "request.json")
    limit = request.get("options", {}).get("rounds", 2)
    if not isinstance(limit, int) or isinstance(limit, bool) or isinstance(round_number, bool) or not 1 <= round_number <= limit:
        raise ValueError("round number outside configured round count")
    caps = child.gate()
    table = read(directory / "table.json") if round_number > 1 and (directory / "table.json").exists() else None
    return request, caps, preview(request, caps["providers"], round_number, table)


def ask(directory, child, round_number=1, *, opt_in=False):
    with ask_lock(directory):
        request, caps, outbound = prepare(directory, child, round_number)
        folder = directory / "rounds" / str(round_number)
        folder.mkdir(parents=True, exist_ok=True)
        key = f"{directory.name}:{round_number}:" + hashlib.sha256(outbound["prompt"].encode()).hexdigest()
        launch_file = folder / "launch.json"
        if launch_file.exists():
            launch = read(launch_file)
            if launch.get("request_key") != key or launch.get("providers") != outbound["providers"]:
                raise ValueError("saved launch request changed; reconcile manually, never relaunch")
            if not launch.get("child_run_id"):
                launch.update(state="unknown", reason="launch_outcome_unknown")
                save(launch_file, launch)
                raise ValueError("child launch outcome unknown; ask the user to reconcile the child run; never relaunch")
            return launch
        if round_number > 1:
            previous = read(directory / "rounds" / str(round_number - 1) / "children.json")
            if previous.get("state") not in {"completed", "partial", "partial_unresolved"} or previous.get("stop_reason"):
                raise ValueError("previous round has not completed or stopped the loop")
        consent = request.get("outbound_consent")
        if consent:
            if (consent.get("field_kinds") != FIELDS or consent.get("providers") != outbound["providers"]
                    or consent.get("domain") != request["domain"]):
                raise ValueError("outbound scope changed; a new explicit decision is required")
        elif not opt_in:
            raise ValueError("explicit opt-in required; review preview then use --opt-in")
        else:
            request["outbound_consent"] = {"field_kinds": FIELDS, "providers": outbound["providers"],
                                            "domain": request["domain"], "recorded_at": now()}
            save(directory / "request.json", request)
        atomic_write(folder / "prompt.md", outbound["prompt"])
        launch = {"request_key": key, "state": "launching", "providers": outbound["providers"],
                  "child_version": caps["version"], "started_at": now(),
                  "deadline": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
        save(launch_file, launch)
        try:
            result = child.launch(outbound["providers"], folder / "prompt.md")
            child_id = result.get("run_id") if isinstance(result, dict) else None
            if not isinstance(child_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", child_id):
                raise ValueError("child returned no valid run_id")
        except (OSError, ValueError, subprocess.SubprocessError):
            launch.update(state="unknown", reason="launch_outcome_unknown")
            save(launch_file, launch)
            raise ValueError("child launch outcome unknown; reconcile manually, never relaunch") from None
        launch.update(state="launched", child_run_id=child_id)
        save(launch_file, launch)
        return launch


def collect(directory, child, round_number=1):
    with ask_lock(directory):
        limit = read(directory / "request.json").get("options", {}).get("rounds", 2)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= round_number <= limit:
            raise ValueError("round number outside configured round count")
        child.gate()
        folder = directory / "rounds" / str(round_number)
        if (folder / "children.json").exists():
            saved = read(folder / "children.json")
            if saved.get("state") in {"completed", "partial", "partial_unresolved"}:
                return saved
        launch = read(folder / "launch.json")
        if not launch.get("child_run_id"):
            raise ValueError("child launch unresolved; never relaunch")
        payload = child.status(launch["child_run_id"])
        statuses = payload.get("providers") if isinstance(payload, dict) else None
        if not isinstance(statuses, dict) or set(statuses) != set(launch["providers"]):
            raise ValueError("child status must contain every selected provider")
        if any(not isinstance(v, dict) or v.get("status") not in TERMINAL | {"queued", "sending", "running"} for v in statuses.values()):
            raise ValueError("invalid provider status")
        states = [v["status"] for v in statuses.values()]
        completed = states.count("completed")
        rest = [s for s in states if s != "completed"]
        terminal = all(s in TERMINAL for s in states)
        deadline_passed = bool(launch.get("deadline") and datetime.now(timezone.utc) >= datetime.fromisoformat(launch["deadline"]))
        finished = terminal or deadline_passed
        state = "waiting" if not finished else "completed" if not rest else "partial_unresolved" if set(rest) == {"sent_unknown"} else "partial"
        record = {"child_run_id": launch["child_run_id"], "providers": statuses, "state": state,
                  "completed_answers": completed, "observed_at": now(), "deadline_passed": deadline_passed}
        if finished and completed == 0:
            record["stop_reason"] = "zero_completed_round_1" if round_number == 1 else "zero_completed_later_round"
            record["next"] = "stop" if round_number == 1 else "verification"
        save(folder / "children.json", record)
        # Delivery reading is deliberately not implemented until its released schema is known.
        # Status completion counts are child claims, not parsed/merged answer evidence.
        return record

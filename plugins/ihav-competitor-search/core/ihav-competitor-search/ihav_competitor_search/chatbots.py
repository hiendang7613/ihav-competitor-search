"""Gated WebChat requests and bounded, immutable delivery intake."""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .homepages import now
from .locking import ask_lock
from .synthesis import synthesize
from .merge import CORE, empty_table, merge_round, parse_answer, validate_answer_record
from .render import atomic_write
from . import research

TERMINAL = {"completed", "failed", "timeout", "login_required",
            "human_verification_required", "not_sent", "sent_unknown", "cancelled"}
FIELDS = research.FIELD_KINDS
INSTALL = ("Choose an ihav-web-chat CLI with the required commands and recovery contracts. "
           "Set --web-chat or IHAV_WEB_CHAT to that CLI and inspect its doctor --json output. "
           "Reload Claude Code or Codex after a plugin upgrade, then rerun the gate.")
MAX_ANSWER_BYTES = 1024 * 1024
PROVIDER_PATTERN = r"[a-z][a-z0-9_-]{0,31}"
RECOVERY_CONTRACTS = {"run_lookup_admission": 1, "launch_outcome": 1}


class ChildCallError(ValueError):
    """Keep bounded structured failures without guessing effects from exit codes."""
    def __init__(self, command, result):
        super().__init__(f"ihav-web-chat {command} failed with exit {result.returncode}")
        self.returncode = result.returncode
        self.stdout = result.stdout[:MAX_ANSWER_BYTES]
        self.stderr = result.stderr[:MAX_ANSWER_BYTES]


def child_run_id(value):
    match = re.fullmatch(r"(\d{8}T\d{6})-[0-9a-f]{8}", value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("invalid child run_id; expected UTC timestamp and eight lowercase hex digits")
    try:
        datetime.strptime(match[1], "%Y%m%dT%H%M%S")
    except ValueError:
        raise ValueError("invalid child run_id timestamp") from None
    return value


def round_folder(directory, number):
    folder = directory / "rounds" / str(number)
    if folder.resolve() != directory.resolve() / "rounds" / str(number):
        raise ValueError("round directory escapes the run")
    return folder


def round_file(folder, name):
    path = folder / name
    if path.resolve() != folder.resolve() / name:
        raise ValueError("round file escapes the run")
    return path


def validate_round(request, number):
    limit = request.get("options", {}).get("rounds", 2)
    if type(limit) is not int or type(number) is not int or not 1 <= number <= limit:
        raise ValueError("round number outside configured round count")


def save(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


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
            raise ChildCallError(' '.join(args[:2]), result)
        return result.stdout

    def gate(self, required=None, *, dispatch=True):
        # Help is local discovery only. Never probe a missing command by launching work.
        required = ({"run", "run lookup", "status", "delivery wait", "delivery read"}
                    if required is None else set(required))
        help_text = self.call("--help")
        names = {command.split()[0] for command in required}
        missing = [name for name in ("run", "status", "delivery") if name in names
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
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            raise ValueError("ihav-web-chat capabilities.commands must be a list of command names")
        missing = sorted(required - set(commands))
        if missing:
            raise ValueError("ihav-web-chat missing commands: " + ", ".join(missing) + ". " + INSTALL)
        providers = caps.get("providers")
        if not isinstance(providers, list) or ("run" in required and not providers) or not all(
                isinstance(p, str) and re.fullmatch(PROVIDER_PATTERN, p) for p in providers) or len(set(providers)) != len(providers):
            raise ValueError("ihav-web-chat capabilities require a nonempty supported provider list")
        if dispatch and ("run" in required or "run lookup" in required):
            contracts = caps.get("contracts")
            needed = RECOVERY_CONTRACTS if "run" in required else {"run_lookup_admission": 1}
            if (not isinstance(contracts, dict) or any(
                    type(contracts.get(name)) is not int or contracts[name] != version
                    for name, version in needed.items())
                    or ("run" in required and (not isinstance(caps.get("default_state_dir"), str)
                        or not Path(caps["default_state_dir"]).is_absolute()))):
                raise ValueError("ihav-web-chat missing versioned recovery contracts and state binding; upgrade the child before dispatch. " + INSTALL)
        return caps

    def launch(self, providers, prompt_file, request_key, state_dir=None):
        state_args = ["--state-dir", str(state_dir)] if state_dir is not None else []
        return json.loads(self.call("run", "--providers", ",".join(providers), "--prompt-file",
                                    str(prompt_file), "--project", str(self.project),
                                    "--request-key", request_key, *state_args, "--json"))

    def launch_targets(self, targets_file, authorization_file, prompt_file, request_key, state_dir):
        return json.loads(self.call("run", "--targets-file", str(targets_file),
                                    "--authorization-file", str(authorization_file), "--prompt-file", str(prompt_file),
                                    "--project", str(self.project), "--request-key", request_key,
                                    "--state-dir", str(state_dir), "--json"))

    def status(self, child_id):
        return json.loads(self.call("status", child_id, "--project", str(self.project), "--json"))

    def lookup(self, request_key, state_dir=None):
        state_args = ["--state-dir", str(state_dir)] if state_dir is not None else []
        return json.loads(self.call("run", "lookup", "--request-key", request_key,
                                    "--project", str(self.project), *state_args, "--json"))

    def delivery(self, child_id):
        return json.loads(self.call("delivery", "read", child_id, "--project", str(self.project),
                                    "--include-text", "--json"))

    def request(self, run_id):
        """Read only the canonical child request inside the selected project."""
        child_run_id(run_id)
        path = self.project / ".ihav_space" / "ihav-web-chat" / "runs" / run_id / "request.json"
        if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_ANSWER_BYTES:
            raise ValueError("child request is missing, escaping, or too large")
        request = read(path)
        allowed = {"schema_version", "run_id", "prompt", "providers", "caller", "request_key", "options", "created_at"}
        version = request.get("schema_version") if isinstance(request, dict) else None
        if type(version) is int and version == 2:
            allowed |= {"targets", "outbound_scope_sha256", "authorization"}
        if (not isinstance(request, dict) or set(request) - allowed
                or type(version) is not int or version not in {1, 2}
                or request.get("run_id") != run_id
                or not isinstance(request.get("prompt"), str) or not request["prompt"].strip()
                or not isinstance(request.get("created_at"), str) or not request["created_at"]):
            raise ValueError("invalid child request schema")
        providers = request.get("providers")
        if (not isinstance(providers, list) or not providers or not all(
                isinstance(p, str) and re.fullmatch(PROVIDER_PATTERN, p) for p in providers)
                or len(set(providers)) != len(providers)):
            raise ValueError("invalid child request providers")
        key = request.get("request_key")
        if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > 256):
            raise ValueError("invalid child request key")
        options = request.get("options", {})
        if not isinstance(options, dict) or any(type(v) not in {str, int, bool} for v in options.values()):
            raise ValueError("invalid child request options")
        caller = request.get("caller")
        if caller is not None and (not isinstance(caller, dict) or set(caller) != {"host", "session"}
                                  or caller.get("host") not in {"claude_code", "codex"}
                                  or not isinstance(caller.get("session"), str) or not caller["session"]):
            raise ValueError("invalid child request caller")
        if version == 2:
            targets = research.target_set({"schema_version": 1, "targets": request.get("targets")})
            if providers != [item["provider"] for item in targets] or key is None or caller is None:
                raise ValueError("invalid typed child request identity")
            research.validate_authorization(request.get("authorization"), prompt=request["prompt"], key=key,
                                             targets=targets, caller=caller)
            if request.get("outbound_scope_sha256") != request["authorization"]["outbound_scope_sha256"]:
                raise ValueError("invalid typed child request scope digest")
        return request


def preview(request, supported, round_number=1, table=None):
    selection = request.get("providers", ["chatgpt", "gemini", "perplexity"])
    providers = list(supported) if selection == "all" else selection
    if (not isinstance(providers, list) or not providers or not all(isinstance(p, str) for p in providers)
            or len(set(providers)) != len(providers)):
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
        prompt += "Use accepted column keys in values without redeclaring those columns.\n"
        prompt += json.dumps({"columns": accepted, "candidates": rows}, ensure_ascii=False, allow_nan=False) + "\n"
    return {"providers": providers, "prompt": prompt, "outbound_field_kinds": FIELDS,
            "domain_sent_verbatim": True}


def prepare(directory, child, round_number, table=None):
    request = read(directory / "request.json")
    validate_round(request, round_number)
    folder = round_folder(directory, round_number)
    launch_file = round_file(folder, "launch.json")
    if launch_file.exists():
        launch = read(launch_file)
        caps = child.gate(set(), dispatch=False)
        validate_scope(request, launch)
        outbound = {"providers": launch["providers"], "prompt": saved_prompt(directory, folder, launch),
                    "outbound_field_kinds": FIELDS, "domain_sent_verbatim": True}
        if launch.get("request_schema_version", 1) == 2:
            outbound.update(targets=launch["targets"], authorization_request=research.authorization_request(
                outbound["prompt"], launch["request_key"], launch["targets"]),
                round_budget=research.budgets(request),
                authorization_basis="persisted_trusted_host_scope_consistency")
        return request, caps, outbound
    caps = child.gate(dispatch=False)
    outbound = preview(request, caps["providers"], round_number, table)
    targets = research.request_targets(request)
    if targets is not None:
        research.require_capabilities(caps, targets)
        key = research.request_key(directory.name, round_number, outbound["prompt"], targets)
        outbound.update(targets=targets, authorization_request=research.authorization_request(outbound["prompt"], key, targets),
                        round_budget=research.budgets(request),
                        authorization_basis="persisted_trusted_host_scope_consistency")
    return request, caps, outbound


def saved_prompt(directory, folder, launch):
    path = folder / "prompt.md"
    if path.resolve() != folder.resolve() / "prompt.md" or not path.is_file() or path.stat().st_size > MAX_ANSWER_BYTES:
        raise ValueError("saved prompt is missing, escaping, or too large")
    prompt = path.read_bytes().decode("utf-8")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if launch.get("request_schema_version", 1) == 2:
        research.validate_launch_prompt(launch, prompt, directory.name, int(folder.name))
        for name, expected in (("targets.json", {"schema_version": 1, "targets": launch["targets"]}),
                               ("authorization.json", launch["authorization"])):
            metadata = round_file(folder, name)
            if not metadata.is_file() or metadata.stat().st_size > research.MAX_METADATA_BYTES or not research.exact(read(metadata), expected):
                raise ValueError("saved research outbound file changed; never relaunch")
        return prompt
    key = f"{directory.name}:{folder.name}:" + digest
    if launch.get("request_key") != key or launch.get("prompt_sha256", digest) != digest:
        raise ValueError("saved launch request changed; reconcile manually, never relaunch")
    return prompt


def validate_identity(child, child_id, launch, prompt):
    request = child.request(child_run_id(child_id))
    if (request.get("request_key") != launch.get("request_key")
            or request.get("providers") != launch.get("providers") or request.get("prompt") != prompt):
        raise ValueError("child request identity mismatch; reconcile manually, never relaunch")
    if request["schema_version"] != launch.get("request_schema_version", 1):
        raise ValueError("child request schema differs from saved launch")
    if launch.get("request_schema_version", 1) == 2 and (
            not research.exact(request["targets"], launch["targets"])
            or not research.exact(request["authorization"], launch["authorization"])
            or request["outbound_scope_sha256"] != launch["outbound_scope_sha256"]):
        raise ValueError("typed child request scope differs from saved launch")


def resolve_launch(directory, folder, child, launch):
    """A lost reply is recovered by exact lookup, never by another launch."""
    prompt = saved_prompt(directory, folder, launch)
    try:
        child_id = launch.get("child_run_id")
        if not child_id:
            state_dir = launch.get("child_state_dir")
            if not isinstance(state_dir, str) or not Path(state_dir).is_absolute():
                raise ValueError("original child state binding missing; do not guess the Store")
            result = child.lookup(launch["request_key"], state_dir)
            ids = result.get("run_ids") if isinstance(result, dict) else None
            if (not isinstance(result, dict) or result.get("request_key") != launch["request_key"]
                    or not isinstance(ids, list) or len(ids) != 1):
                raise ValueError("child launch outcome unknown; lookup must return exactly one matching run")
            child_id = child_run_id(ids[0])
            proof = result.get("runs")
            if (type(result.get("admission_schema_version")) is not int
                    or result["admission_schema_version"] != 1
                    or not isinstance(proof, list) or len(proof) != 1
                    or not isinstance(proof[0], dict) or proof[0].get("run_id") != child_id
                    or proof[0].get("admission") != "admitted"
                    or not isinstance(result.get("state_dir"), str)
                    or not Path(result["state_dir"]).is_absolute()
                    or (launch.get("child_state_dir") is not None
                        and launch["child_state_dir"] != result["state_dir"])):
                raise ValueError("child lookup has no matching durable admission proof")
            providers = proof[0].get("providers")
            digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            if (not isinstance(providers, list) or len(providers) != len(launch["providers"])
                    or any(not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                           or entry["name"] not in launch["providers"]
                           or entry.get("job_id") != child_id + "/" + entry["name"]
                           or entry.get("prompt_sha256") != digest for entry in providers)
                    or {entry["name"] for entry in providers} != set(launch["providers"])):
                raise ValueError("child admission jobs do not match the saved request")
            if launch.get("request_schema_version", 1) == 2:
                admission = proof[0]
                if (type(admission.get("request_schema_version")) is not int or admission["request_schema_version"] != 2
                        or admission.get("outbound_scope_sha256") != launch["outbound_scope_sha256"]
                        or admission.get("authorization_sha256") != launch["authorization_sha256"]):
                    raise ValueError("child admission has no matching typed scope")
                for entry in providers:
                    target = next(item for item in launch["targets"] if item["provider"] == entry["name"])
                    outbound = next(item for item in launch["authorization"]["targets"] if item["provider"] == entry["name"])
                    if not research.exact(entry.get("target"), target) or entry.get("outbound_sha256") != outbound["outbound_sha256"]:
                        raise ValueError("child admitted job target differs from saved launch")
        validate_identity(child, child_id, launch, prompt)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        launch.update(state="unknown", reason="launch_identity_unresolved")
        save(folder / "launch.json", launch)
        raise ValueError("child launch outcome unknown; reconcile child identity, never relaunch") from None
    if launch.get("state") != "launched" or not launch.get("child_run_id"):
        launch.update(state="launched", child_run_id=child_id, recovered_at=now())
        launch.pop("reason", None)
        save(folder / "launch.json", launch)
    return launch


def pre_admission_refusal(error, caps):
    if caps.get("contracts", {}).get("launch_outcome") != 1:
        return None
    try:
        value = json.loads(error.stdout)
    except (ValueError, TypeError, RecursionError):
        return None
    if (not isinstance(value, dict) or value.get("outcome") != "refused"
            or value.get("phase") != "pre_admission" or value.get("effect") != "none"
            or not isinstance(value.get("code"), str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value["code"])
            or not isinstance(value.get("message"), str)):
        return None
    try:
        if len(value["message"].encode("utf-8")) > 4096:
            return None
    except UnicodeEncodeError:
        return None
    return {key: value[key] for key in ("outcome", "phase", "effect", "code", "message")}


def refused_launch(launch):
    return ValueError("child launch refused before queue admission: " + str(launch.get("reason", "refused"))
                      + "; create a new run with corrected scope; this launch will not be replayed")


def validate_scope(request, launch):
    if research.request_targets(request) is not None:
        research.validate_launch_scope(request, launch)
        return
    if launch.get("request_schema_version", 1) != 1:
        raise ValueError("legacy parent scope cannot acquire a research launch")
    selected = launch.get("providers")
    selection = request.get("providers", ["chatgpt", "gemini", "perplexity"])
    consent = request.get("outbound_consent")
    if (not isinstance(selected, list) or not selected or not all(
            isinstance(p, str) and re.fullmatch(PROVIDER_PATTERN, p) for p in selected)
            or len(set(selected)) != len(selected) or (selection != "all" and selection != selected)
            or not isinstance(consent, dict) or consent.get("field_kinds") != FIELDS
            or consent.get("providers") != selected or consent.get("domain") != request["domain"]
            or launch.get("providers") != selected
            or launch.get("domain", request["domain"]) != request["domain"]):
        raise ValueError("saved launch request changed; outbound scope changed; never relaunch")


def previous_child_ready(directory, folder):
    """Check saved child work without recovery, dispatch or another child call."""
    try:
        launch = read(round_file(folder, "launch.json"))
        record = read(round_file(folder, "children.json"))
        if (not isinstance(launch, dict) or launch.get("state") != "launched"
                or not isinstance(record, dict) or not isinstance(record.get("state"), str)
                or record["state"] not in {"completed", "partial"} or record.get("stop_reason")
                or type(record.get("parsed_answers")) is not int or record["parsed_answers"] <= 0
                or record.get("child_run_id") != launch.get("child_run_id")):
            raise ValueError("unresolved child phase")
        child_run_id(launch.get("child_run_id"))
        providers = launch.get("providers")
        if (not isinstance(providers, list) or not providers or not all(
                isinstance(p, str) and re.fullmatch(PROVIDER_PATTERN, p) for p in providers)
                or len(set(providers)) != len(providers)):
            raise ValueError("invalid child providers")
        saved_prompt(directory, folder, launch)
        statuses = provider_statuses({"run_id": record["child_run_id"], "providers": record.get("providers")}, launch)
        if any(v["status"] not in TERMINAL or v["status"] == "sent_unknown" for v in statuses.values()):
            raise ValueError("unresolved child provider")
        usable = 0
        saved_answers = []
        for provider, entry in statuses.items():
            answer = existing_answer(answer_path(directory, folder, provider), provider, launch)
            if answer is not None:
                saved_answers.append(answer)
            if entry["status"] == "completed" and answer is not None and answer["status"] == "completed":
                usable += 1
        if usable == 0 or usable != record["parsed_answers"]:
            raise ValueError("child has no matching usable saved answers")
        if launch.get("request_schema_version", 1) == 2 and not research.collection_summary(launch, saved_answers, record)["requirements_met"]:
            raise ValueError("previous research round has incomplete matching receipts")
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError("previous round has not completed or stopped the loop; reconcile saved child work") from None
    return launch


def ensure_round_mutable(directory, number):
    """An existing later round holds the prior answers it was derived from fixed."""
    root = directory / "rounds"
    if root.resolve() != directory.resolve() / "rounds":
        raise ValueError("rounds directory escapes the run")
    for folder in root.iterdir() if root.exists() else []:
        if (not folder.is_dir() or folder.resolve() != root.resolve() / folder.name
                or not re.fullmatch(r"[1-9][0-9]*", folder.name)):
            raise ValueError("round folders must be contained positive integers")
        if int(folder.name) > number and (
                any((folder / name).exists() for name in ("launch.json", "children.json", "prompt.md"))
                or any((folder / "answers").glob("*.json"))):
            raise ValueError("later round depends on these answers; reconcile before changing an earlier round")


def previous_round_table(directory, previous_number, request, *, manual_only=False):
    """Rebuild the consecutive, usable predecessor chain without external calls."""
    previous_folder = round_folder(directory, previous_number)
    if manual_only and round_file(previous_folder, "launch.json").exists():
        raise ValueError("previous child round requires collection before another launch")
    rounds_root = directory / "rounds"
    if rounds_root.resolve() != directory.resolve() / "rounds":
        raise ValueError("rounds directory escapes the run")
    recorded = list(rounds_root.iterdir()) if rounds_root.exists() else []
    if any(not folder.is_dir() or folder.resolve() != rounds_root.resolve() / folder.name
           or not re.fullmatch(r"[1-9][0-9]*", folder.name) for folder in recorded):
        raise ValueError("round folders must be contained positive integers")
    numbers = sorted(int(folder.name) for folder in recorded if int(folder.name) <= previous_number)
    if len(numbers) != previous_number or any(number != index for index, number in enumerate(numbers, 1)):
        raise ValueError("previous round has no usable manual answers")
    rebuilt = empty_table()
    options = request.get("options", {})
    for number in numbers:
        folder = round_folder(directory, number)
        launch_file = round_file(folder, "launch.json")
        children_file = round_file(folder, "children.json")
        launch = previous_child_ready(directory, folder) if launch_file.exists() else None
        if launch is None and children_file.exists():
            raise ValueError("previous round has child status without its launch; reconcile saved child work")
        answers_dir = folder / "answers"
        if answers_dir.resolve() != directory.resolve() / "rounds" / str(number) / "answers":
            raise ValueError("previous answers directory escapes the run")
        answers = []
        for path in sorted(answers_dir.glob("*.json")):
            if path.resolve() != answers_dir.resolve() / path.name:
                raise ValueError("previous answer file escapes the run")
            record = validate_answer_record(read(path), path.stem, launch=launch)
            if manual_only and number == previous_number:
                if record.get("method") != "manual_paste" or "status" not in record:
                    raise ValueError("previous round requires canonical manual_paste answers")
            answers.append(record)
        if not answers:
            raise ValueError("previous round has no usable answers")
        try:
            rebuilt = merge_round(rebuilt, answers, number,
                                  max_new=options.get("max_new", 30),
                                  max_new_columns=options.get("max_new_columns", 5),
                                  max_total_columns=options.get("max_total_columns", 25))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ValueError("previous round contains invalid saved answers") from None
        progress = rebuilt["rounds"][-1]
        if progress["stop"]:
            raise ValueError("previous round stopped the loop")
    if not any(p["status"] == "completed" for p in rebuilt["rounds"][-1]["providers"]):
        raise ValueError("previous round has no usable answers")
    return rebuilt


def manual_previous_ready(directory, previous_number, request, table):
    """Require fresh synthesis before switching a canonical manual round to a child."""
    rebuilt = previous_round_table(directory, previous_number, request, manual_only=True)
    if (not isinstance(table, dict) or table.get("rounds") != rebuilt["rounds"]
            or not rebuilt["rounds"] or rebuilt["rounds"][-1]["round"] != previous_number
            or not any(p["status"] == "completed" for p in rebuilt["rounds"][-1]["providers"])):
        raise ValueError("previous manual round requires fresh synthesis with usable answers")


def ask(directory, child, round_number=1, *, opt_in=False, table=None):
    with ask_lock(directory):
        request = read(directory / "request.json")
        validate_round(request, round_number)
        folder = round_folder(directory, round_number)
        answer_dir = folder / "answers"
        if answer_dir.resolve() != directory.resolve() / "rounds" / str(round_number) / "answers":
            raise ValueError("answer directory escapes the run")
        for path in answer_dir.glob("*.json"):
            if path.resolve() != answer_dir.resolve() / path.name:
                raise ValueError("answer file escapes the run")
            if read(path).get("method") == "manual_paste":
                raise ValueError("round has manual_paste answers; use a separate round for automated launch")
        launch_file = round_file(folder, "launch.json")
        if launch_file.exists():
            launch = read(launch_file)
            if launch.get("state") == "refused":
                raise refused_launch(launch)
            child.gate({"run lookup"} if not launch.get("child_run_id") else set())
            validate_scope(request, launch)
            return resolve_launch(directory, folder, child, launch)
        ensure_round_mutable(directory, round_number)
        caps = child.gate()
        outbound = preview(request, caps["providers"], round_number, table)
        targets = research.request_targets(request)
        if targets is not None:
            research.require_capabilities(caps, targets)
        if round_number > 1:
            previous_file = round_file(round_folder(directory, round_number - 1), "children.json")
            if targets is not None and not previous_file.exists():
                raise ValueError("research progression requires matching receipts for every predecessor")
            if previous_file.exists():
                previous_round_table(directory, round_number - 1, request)
            else:
                manual_previous_ready(directory, round_number - 1, request, table)
            if targets is not None:
                for prior_folder in sorted((directory / "rounds").iterdir(), key=lambda path: int(path.name)):
                    if int(prior_folder.name) < round_number:
                        if not round_file(prior_folder, "launch.json").exists():
                            raise ValueError("research progression requires matching receipts for every predecessor")
                        validate_scope(request, previous_child_ready(directory, prior_folder))
            # Answer intake and cell edits hold this same lock. Compare the
            # exact canonical projection, including accepted host corrections,
            # before saving intent; summary counts alone cannot establish it.
            current, _ = synthesize(directory, through_round=round_number - 1)
            if preview(request, caps["providers"], round_number, current)["prompt"] != outbound["prompt"]:
                raise ValueError("previous round context changed; review a fresh preview before launching")
        consent = request.get("outbound_consent")
        if targets is not None:
            if consent:
                research.validate_consent(request, FIELDS, active=True)
            elif not opt_in:
                raise ValueError("explicit opt-in required; review research targets and round budget")
            else:
                request["outbound_consent"] = {**research.consent_scope(request, FIELDS, research.trusted_caller()),
                                                "recorded_at": now()}
                save(directory / "request.json", request)
        elif consent:
            if (consent.get("field_kinds") != FIELDS or consent.get("providers") != outbound["providers"]
                    or consent.get("domain") != request["domain"]):
                raise ValueError("outbound scope changed; a new explicit decision is required")
        elif not opt_in:
            raise ValueError("explicit opt-in required; review preview then use --opt-in")
        else:
            request["outbound_consent"] = {"field_kinds": FIELDS, "providers": outbound["providers"],
                                            "domain": request["domain"], "recorded_at": now()}
            save(directory / "request.json", request)
        folder.mkdir(parents=True, exist_ok=True)
        key = (research.request_key(directory.name, round_number, outbound["prompt"], targets) if targets is not None
               else f"{directory.name}:{round_number}:" + hashlib.sha256(outbound["prompt"].encode()).hexdigest())
        atomic_write(folder / "prompt.md", outbound["prompt"])
        launch = {"request_key": key, "state": "launching", "providers": outbound["providers"],
                  "domain": request["domain"], "prompt_sha256": hashlib.sha256(outbound["prompt"].encode()).hexdigest(),
                  "child_version": caps["version"], "child_state_dir": caps["default_state_dir"],
                  "child_contracts": {name: caps["contracts"][name] for name in RECOVERY_CONTRACTS}, "started_at": now(),
                  "deadline": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
        if targets is not None:
            authorization = {**research.authorization_request(outbound["prompt"], key, targets),
                             "issuer": request["outbound_consent"]["issuer"], "issued_at": now()}
            research.validate_authorization(authorization, prompt=outbound["prompt"], key=key,
                                             targets=targets, caller=request["outbound_consent"]["issuer"])
            launch.update(request_schema_version=2, targets=targets, authorization=authorization,
                          consent_sha256=research.digest(request["outbound_consent"]),
                          authorization_sha256=research.digest(authorization),
                          outbound_scope_sha256=authorization["outbound_scope_sha256"])
            launch["child_contracts"].update(research.CONTRACTS)
            save(round_file(folder, "targets.json"), {"schema_version": 1, "targets": targets})
            save(round_file(folder, "authorization.json"), authorization)
        save(launch_file, launch)
        try:
            result = (child.launch_targets(folder / "targets.json", folder / "authorization.json", folder / "prompt.md",
                                           key, launch["child_state_dir"]) if targets is not None
                      else child.launch(outbound["providers"], folder / "prompt.md", key, launch["child_state_dir"]))
            child_id = result.get("run_id") if isinstance(result, dict) else None
            if not isinstance(result, dict) or result.get("state_dir") != launch["child_state_dir"]:
                raise ValueError("child launch state binding mismatch")
            validate_identity(child, child_id, launch, outbound["prompt"])
        except ChildCallError as exc:
            refusal = pre_admission_refusal(exc, caps)
            if refusal is not None:
                if refusal["code"] == "duplicate_request_key":
                    launch.update(state="unknown", reason="prior_keyed_request_requires_reconciliation",
                                  refusal=refusal)
                    save(launch_file, launch)
                    return resolve_launch(directory, folder, child, launch)
                launch.update(state="refused", reason=refusal["code"], refusal=refusal, refused_at=now())
                save(launch_file, launch)
                raise refused_launch(launch) from None
            launch.update(state="unknown", reason="launch_outcome_unknown")
            save(launch_file, launch)
            raise ValueError("child launch outcome unknown; reconcile manually, never relaunch") from None
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            launch.update(state="unknown", reason="launch_outcome_unknown")
            save(launch_file, launch)
            raise ValueError("child launch outcome unknown; reconcile manually, never relaunch") from None
        launch.update(state="launched", child_run_id=child_id)
        save(launch_file, launch)
        return launch


def provider_statuses(payload, launch):
    statuses = payload.get("providers") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get("run_id") != launch["child_run_id"]
            or not isinstance(statuses, dict) or set(statuses) != set(launch["providers"])):
        raise ValueError("child status must identify the run and every selected provider")
    if any(not isinstance(v, dict) or not isinstance(v.get("status"), str)
           or v["status"] not in TERMINAL | {"queued", "sending", "running"}
           or (v.get("reason") is not None and not isinstance(v.get("reason"), str))
           or not isinstance(v.get("updated_at"), str) or not v["updated_at"] for v in statuses.values()):
        raise ValueError("invalid provider status")
    return {provider: {key: value[key] for key in ("status", "reason", "updated_at") if key in value}
            for provider, value in statuses.items()}


def answer_path(directory, folder, provider):
    if not isinstance(provider, str) or not re.fullmatch(PROVIDER_PATTERN, provider):
        raise ValueError("invalid answer provider")
    path = folder / "answers" / (provider + ".json")
    if path.resolve() != directory.resolve() / "rounds" / folder.name / "answers" / (provider + ".json"):
        raise ValueError("answer path escapes the run")
    return path


def existing_answer(path, provider, launch):
    if not path.exists():
        return None
    record = read(path)
    raw = record.get("raw") if isinstance(record, dict) else None
    if (not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_ANSWER_BYTES
            or record.get("method") != "web_chat_delivery" or record.get("provider") != provider
            or record.get("child_run_id") != launch["child_run_id"]
            or record.get("request_key") != launch["request_key"]
            or record.get("raw_sha256") != hashlib.sha256(raw.encode("utf-8")).hexdigest()
            or not isinstance(record.get("status"), str) or record["status"] not in {"completed", "parse_failed"}
            or not isinstance(record.get("fetched_at"), str) or not record["fetched_at"]):
        raise ValueError("saved answer identity changed; reconcile without overwriting")
    if record["status"] == "completed":
        parse_answer(raw)
    research.validate_saved_answer(record, launch)
    return record


def ingest_answer(path, provider, launch, raw, execution_receipt=None):
    prior = existing_answer(path, provider, launch)
    preserved = {"parsed": bool(prior and prior["status"] == "completed"), "preserved": prior is not None}
    if prior is not None:
        preserved["raw_sha256"] = prior["raw_sha256"]
    if not isinstance(raw, str) or not raw.strip():
        return {"status": "unreadable", "reason": "completed_response_missing_or_empty", **preserved}
    if len(raw.encode("utf-8")) > MAX_ANSWER_BYTES:
        return {"status": "unreadable", "reason": "answer_exceeds_1_mib", **preserved}
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    typed = launch.get("request_schema_version", 1) == 2
    if typed:
        research.bounded(execution_receipt)
        receipt_digest = research.digest(execution_receipt)
    if prior is not None:
        if prior["raw_sha256"] != digest:
            raise ValueError("delivered answer changed; reconcile without overwriting")
        if typed and (prior["execution_receipt_sha256"] != receipt_digest
                      or not research.exact(prior["execution_receipt"], execution_receipt)):
            raise ValueError("delivered execution receipt changed; reconcile without overwriting")
        return {"status": prior["status"], "raw_sha256": digest,
                "parsed": prior["status"] == "completed", "preserved": True,
                **({"research_qualification": prior["research_qualification"]} if typed else {})}
    record = {"provider": provider, "method": "web_chat_delivery", "child_run_id": launch["child_run_id"],
              "request_key": launch["request_key"], "raw_sha256": digest, "fetched_at": now(), "raw": raw}
    if typed:
        record.update(execution_receipt=execution_receipt, execution_receipt_sha256=receipt_digest,
                      research_qualification=research.qualification(execution_receipt, launch, provider, raw))
    try:
        parse_answer(raw)
        record["status"] = "completed"
    except (TypeError, ValueError, RecursionError) as exc:
        record.update(status="parse_failed", reason=str(exc))
    path.parent.mkdir(parents=True, exist_ok=True)
    save(path, record)
    return {"status": record["status"], "raw_sha256": digest,
            "parsed": record["status"] == "completed", **({"reason": record["reason"]} if "reason" in record else {}),
            **({"research_qualification": record["research_qualification"]} if typed else {})}


def collect(directory, child, round_number=1):
    with ask_lock(directory):
        request = read(directory / "request.json")
        validate_round(request, round_number)
        folder = round_folder(directory, round_number)
        launch = read(round_file(folder, "launch.json"))
        children_file = round_file(folder, "children.json")
        if launch.get("state") == "refused":
            raise refused_launch(launch)
        required = {"status", "delivery read"}
        if not launch.get("child_run_id"):
            required.add("run lookup")
        child.gate(required)
        validate_scope(request, launch)
        launch = resolve_launch(directory, folder, child, launch)
        payload = child.status(launch["child_run_id"])
        statuses = provider_statuses(payload, launch)
        delivery = None
        delivery_error = None
        if any(v["status"] == "completed" for v in statuses.values()):
            try:
                delivery = child.delivery(launch["child_run_id"])
                statuses = provider_statuses(delivery, launch)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                delivery_error = "delivery_unreadable"
                delivery = None
        ingestion = {}
        for provider, entry in statuses.items():
            path = answer_path(directory, folder, provider)
            prior = existing_answer(path, provider, launch)
            if entry["status"] != "completed":
                ingestion[provider] = {"status": "not_completed", "parsed": False}
            elif delivery_error:
                ingestion[provider] = {"status": "unreadable", "reason": delivery_error,
                                       "parsed": bool(prior and prior["status"] == "completed"),
                                       "preserved": prior is not None}
            else:
                try:
                    ingestion[provider] = ingest_answer(path, provider, launch,
                                                        delivery["providers"][provider].get("response_text"),
                                                        delivery["providers"][provider].get("execution_receipt"))
                except ValueError:
                    if launch.get("request_schema_version", 1) == 2:
                        save(children_file, {"child_run_id": launch["child_run_id"], "providers": statuses,
                                             "state": "reconciliation_required", "parsed_answers": 0,
                                             "ingestion": ingestion, "stop_reason": "delivery_identity_changed",
                                             "observed_at": now()})
                    raise
        states = [v["status"] for v in statuses.values()]
        completed = states.count("completed")
        parsed = sum(v["parsed"] for v in ingestion.values())
        rest = [s for s in states if s != "completed"]
        terminal = all(s in TERMINAL for s in states)
        deadline_passed = bool(launch.get("deadline") and datetime.now(timezone.utc) >= datetime.fromisoformat(launch["deadline"]))
        state = ("waiting" if not terminal else "partial_unresolved" if "sent_unknown" in rest else
                 "completed" if not rest and parsed == completed
                 and all(v["status"] == "completed" for v in ingestion.values()) else "partial")
        record = {"child_run_id": launch["child_run_id"], "providers": statuses, "state": state,
                  "completed_answers": completed, "parsed_answers": parsed, "ingestion": ingestion,
                  "observed_at": now(), "deadline_passed": deadline_passed}
        if launch.get("request_schema_version", 1) == 2:
            answers = [existing_answer(answer_path(directory, folder, provider), provider, launch)
                       for provider in launch["providers"]]
            record["research"] = research.collection_summary(launch, [answer for answer in answers if answer is not None], record)
        if not terminal:
            record["next"] = "wait"
        elif parsed == 0:
            prefix = "zero_completed" if completed == 0 else "zero_parsed"
            record["stop_reason"] = prefix + ("_round_1" if round_number == 1 else "_later_round")
            record["next"] = "stop" if round_number == 1 else "verification"
        save(children_file, record)
        return record

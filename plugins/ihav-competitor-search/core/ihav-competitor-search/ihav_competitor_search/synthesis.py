"""Canonical offline synthesis of saved answers and accepted host evidence."""
import json
import re

from .homepages import apply_homepage_decisions
from .merge import empty_table, merge_round, validate_answer_record
from .rank import apply_visits
from .verification import apply_verifications
from . import research


def load(path):
    if path.is_symlink():
        raise ValueError("saved input file must not be a symlink")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {v}")))
    except RecursionError as exc:
        raise ValueError("saved JSON nesting exceeds the supported limit") from exc


def synthesize(directory, *, through_round=None):
    directory = directory.resolve()
    request = load(directory / "request.json")
    options = request.get("options", {})
    targets = research.request_targets(request)
    research_rounds = {}
    table = empty_table()
    rounds_root = directory / "rounds"
    if rounds_root.resolve() != rounds_root:
        raise ValueError("rounds directory escapes the run")
    rounds = list(rounds_root.iterdir()) if rounds_root.exists() else []
    if any(not folder.is_dir() or folder.resolve() != folder or not re.fullmatch(r"[1-9][0-9]*", folder.name) for folder in rounds):
        raise ValueError("round folders must be positive integers")
    rounds.sort(key=lambda p: int(p.name))
    if through_round is not None:
        rounds = [folder for folder in rounds if int(folder.name) <= through_round]
    limit = options.get("rounds", 2)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("rounds must be a positive integer")
    snapshots = []
    merge_stopped = False
    # Table growth stops on its existing budget/no-progress rule. Research
    # evidence must still cover later recorded intent or collection artifacts.
    for index, folder in enumerate(rounds if targets is not None else rounds[:limit]):
        answer_dir = folder / "answers"
        if answer_dir.resolve() != answer_dir:
            raise ValueError("answers directory escapes the run")
        launch = load(folder / "launch.json") if (folder / "launch.json").exists() else None
        if (folder / "launch.json").exists() and not isinstance(launch, dict):
            raise ValueError("invalid saved answer launch; expected an object")
        if launch is not None and launch.get("request_schema_version", 1) == 2:
            if targets is None:
                raise ValueError("legacy survey cannot acquire research attribution")
            research.validate_launch_scope(request, launch)
            prompt_path = folder / "prompt.md"
            if prompt_path.resolve() != folder / "prompt.md" or prompt_path.stat().st_size > research.MAX_METADATA_BYTES:
                raise ValueError("saved research prompt is missing, escaping, or too large")
            research.validate_launch_prompt(launch, prompt_path.read_bytes().decode("utf-8"), directory.name, int(folder.name))
        answers = [validate_answer_record(load(p), p.stem, launch=launch)
                   for p in sorted(answer_dir.glob("*.json"))]
        if targets is not None:
            recorded = bool(answers) or any((folder / name).exists() for name in (
                "launch.json", "children.json", "prompt.md", "targets.json", "authorization.json"))
            if recorded:
                child = load(folder / "children.json") if (folder / "children.json").exists() else None
                research_rounds[folder.name] = research.collection_summary(launch, answers, child)
            if merge_stopped or index >= limit:
                continue
        if not answers:
            continue
        if int(folder.name) != len(table["rounds"]) + 1:
            raise ValueError("previous round has no usable answers; saved rounds must be consecutive")
        table = merge_round(table, answers, int(folder.name),
                            max_new=options.get("max_new", 30),
                            max_new_columns=options.get("max_new_columns", 5),
                            max_total_columns=options.get("max_total_columns", 25))
        snapshots.append((folder.name, json.dumps(table, ensure_ascii=False, indent=2, allow_nan=False) + "\n"))
        if table["rounds"][-1]["stop"]:
            if targets is None:
                break
            merge_stopped = True
    table = apply_homepage_decisions(table, request, directory)
    visits = load(directory / "visits.json") if (directory / "visits.json").exists() else {}
    table = apply_visits(table, visits)
    table = apply_verifications(table, directory, top=options.get("verify_top", 50))
    table["chatbot_rounds"] = {folder.name: load(folder / "children.json") for folder in rounds
                               if (folder / "children.json").exists()}
    table["request"] = request
    if targets is not None:
        recorded = [int(number) for number in research_rounds]
        observed = [int(number) for number, summary in research_rounds.items() if summary["requirements_met"]]
        met = (len(recorded) >= 2 and recorded == list(range(1, len(recorded) + 1))
               and recorded[-1] <= limit and observed == recorded)
        table["research"] = {"state": "observed" if met else "unverified", "targets": targets,
                             "rounds": research_rounds, "observed_rounds": observed,
                             "successive_rounds_required": 2, "requirements_met": met,
                             "evidence_basis": "saved_child_execution_receipts",
                             "cell_verification_is_separate": True}
    table["stages"] = {"ask": "recorded_input", "merge": "completed", "rank": "recorded_input",
                       "homepage_check": "recorded_input", "verification": "host_supplied_page_evidence"}
    return table, snapshots

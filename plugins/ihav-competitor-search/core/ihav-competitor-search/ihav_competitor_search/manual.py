"""Local copy/paste answers; never discovers or calls a chatbot child."""
import re

from .chatbots import ask_lock, preview, read, save
from .homepages import now
from .merge import parse_answer


def validate_round(directory, number):
    request = read(directory / "request.json")
    limit = request.get("options", {}).get("rounds", 2)
    if (type(limit) is not int or type(number) is not int or not 1 <= number <= limit):
        raise ValueError("round number outside configured round count")
    return request


def prompt(directory, number, table=None):
    request = validate_round(directory, number)
    # Provider selection belongs to the person copying the prompt, not a child.
    return preview({**request, "providers": ["manual"]}, ["manual"], number, table)["prompt"]


def import_answer(directory, number, provider, raw, *, replace=False):
    validate_round(directory, number)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", provider):
        raise ValueError("provider must be a safe name of at most 128 characters")
    with ask_lock(directory):
        folder = directory / "rounds" / str(number)
        answers = folder / "answers"
        if answers.resolve() != directory.resolve() / "rounds" / str(number) / "answers":
            raise ValueError("answer directory escapes the run")
        if (folder / "launch.json").exists():
            raise ValueError("round has a child launch; reconcile before importing manual answers")
        path = answers / f"{provider}.json"
        if path.exists() and not replace:
            raise ValueError("answer already recorded; use --replace to replace it")
        record = {"provider": provider, "method": "manual_paste", "fetched_at": now(), "raw": raw}
        try:
            parse_answer(raw)
            record["status"] = "completed"
        except (ValueError, TypeError) as exc:
            record.update(status="parse_failed", reason=str(exc))
        answers.mkdir(parents=True, exist_ok=True)
        save(path, record)
        return record

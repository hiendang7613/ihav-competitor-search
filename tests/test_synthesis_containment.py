import json

import pytest

from ihav_competitor_search import cli
from ihav_competitor_search.survey import create_run


@pytest.mark.parametrize("escape", ["request", "rounds", "round", "answers", "answer"])
@pytest.mark.parametrize("command", ["status", "prompt", "ask"])
def test_saved_input_symlinks_cannot_enter_later_prompt(tmp_path, monkeypatch, capsys, escape, command):
    directory = create_run(tmp_path, "domain", run_id="bounded")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = {"provider": "private", "raw": {"columns": [], "candidates": [{"name": "PRIVATE SENTINEL", "homepage": "https://private.example", "values": {}}]}}
    (outside / "private.json").write_text(json.dumps(sentinel))
    folder = directory / "rounds/1"
    if escape == "request":
        (directory / "request.json").unlink()
        (directory / "request.json").symlink_to(outside / "private.json")
    elif escape == "rounds":
        (directory / "rounds").symlink_to(outside, target_is_directory=True)
    elif escape == "round":
        folder.parent.mkdir()
        folder.symlink_to(outside, target_is_directory=True)
    else:
        folder.mkdir(parents=True)
        answers = folder / "answers"
        if escape == "answers":
            answers.symlink_to(outside, target_is_directory=True)
        else:
            answers.mkdir()
            (answers / "private.json").symlink_to(outside / "private.json")
    def forbidden(*args, **kwargs):
        raise AssertionError("child must not be constructed for escaped saved input")
    monkeypatch.setattr(cli, "WebChatChild", forbidden)
    args = ["--project", str(tmp_path), command, "bounded"]
    if command in {"prompt", "ask"}:
        args += ["--round", "2"]
    assert cli.main(args) == 2
    assert "PRIVATE SENTINEL" not in capsys.readouterr().out
    assert not (directory / "rounds/2").exists()


def test_render_refuses_outside_synthesis_without_replacing_outputs(tmp_path, capsys):
    from ihav_competitor_search.manual import import_answer
    directory = create_run(tmp_path, "domain", run_id="bounded")
    import_answer(directory, 1, "manual", '{"columns":[],"candidates":[]}')
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "1.json").write_text("outside sentinel")
    (directory / "table.json").write_text("previous output")
    (directory / "synthesis").symlink_to(outside, target_is_directory=True)
    assert cli.main(["--project", str(tmp_path), "render", "bounded"]) == 2
    assert (outside / "1.json").read_text() == "outside sentinel"
    assert (directory / "table.json").read_text() == "previous output"
    capsys.readouterr()

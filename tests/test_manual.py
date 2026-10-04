import io
import json
import socket
from pathlib import Path

import pytest

from ihav_competitor_search import cli
from ihav_competitor_search.chatbots import ask
from ihav_competitor_search.homepages import HTTPHomepageStage
from ihav_competitor_search.manual import import_answer


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(cli, "WebChatChild", forbidden)


@pytest.fixture
def run(tmp_path):
    directory = tmp_path / ".ihav_space/ihav-competitor-search/runs/manual"
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps({"domain": "Design tools\nverbatim", "providers": "all",
        "options": {"rounds": 2, "max_new_columns": 3}}))
    return directory


def raw(name="Alpha", host="alpha.example"):
    return json.dumps({"columns": [], "candidates": [{"name": name, "homepage": f"https://{host}", "values": {}}]})


def args(run):
    return ["--project", str(run.parents[3])]


def test_prompt_exact_readonly_and_fresh_round_two(run, capsys):
    assert cli.main(args(run) + ["prompt", "manual"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("Survey products or services")
    assert "Design tools\nverbatim\n" in text
    assert "propose up to 3 extra columns" in text
    assert list(run.iterdir()) == [run / "request.json"]
    assert cli.main(args(run) + ["prompt", "manual", "--round", "2"]) == 2
    capsys.readouterr()
    import_answer(run, 1, "chatgpt", raw())
    (run / "table.json").write_text("stale output ignored")
    assert cli.main(args(run) + ["prompt", "manual", "--round", "2"]) == 0
    text = capsys.readouterr().out
    assert "Return only new candidates absent from this table; propose only new columns." in text
    assert '"name": "Alpha"' in text and '"homepage": "https://alpha.example"' in text
    assert "manual_paste" not in text  # projected values, not internal provenance
    assert "outbound_consent" not in json.loads((run / "request.json").read_text())


@pytest.mark.parametrize("fenced", [False, True])
def test_file_and_stdin_imports_keep_exact_raw(run, tmp_path, monkeypatch, capsys, fenced):
    text = "```json\n" + raw() + "\n```" if fenced else raw() + "\r\n"
    if fenced:
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(text))
        extra = []
    else:
        path = tmp_path / "answer with spaces.json"
        path.write_bytes(text.encode("utf-8"))
        extra = ["--file", str(path)]
    assert cli.main(args(run) + ["answer", "manual", "--round", "1", "--provider", "ChatGPT", *extra]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["raw"] == text and record["status"] == "completed"
    assert record["method"] == "manual_paste" and record["fetched_at"]
    assert record == json.loads((run / "rounds/1/answers/ChatGPT.json").read_text())


def test_parse_failure_duplicate_replace_and_path_validation(run):
    record = import_answer(run, 1, "gemini", "not JSON")
    assert record["status"] == "parse_failed" and record["raw"] == "not JSON"
    path = run / "rounds/1/answers/gemini.json"
    original = path.read_bytes()
    with pytest.raises(ValueError, match="already recorded"):
        import_answer(run, 1, "gemini", raw())
    assert path.read_bytes() == original
    import_answer(run, 1, "gemini", raw(), replace=True)
    assert json.loads(path.read_text())["status"] == "completed"
    for provider in ("../escape", "a/b", "", "a" * 129):
        with pytest.raises(ValueError, match="provider"):
            import_answer(run, 1, provider, raw())
    with pytest.raises(ValueError, match="round"):
        import_answer(run, 3, "test", raw())


def test_cli_parse_failure_saved_with_nonzero_exit(run, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("broken"))
    assert cli.main(args(run) + ["answer", "manual", "--round", "1", "--provider", "bad"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "parse_failed"
    assert cli.synthesize(run)[0]["rounds"][0]["providers"][0]["method"] == "manual_paste"


def test_no_mixed_child_launch_or_symlink_escape(run, tmp_path):
    folder = run / "rounds/1"
    folder.mkdir(parents=True)
    (folder / "launch.json").write_text("{}")
    with pytest.raises(ValueError, match="child launch"):
        import_answer(run, 1, "test", raw())
    (folder / "launch.json").unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder / "answers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        import_answer(run, 1, "test", raw())
    assert not list(outside.iterdir())


def test_manual_end_to_end_two_providers_and_rounds(run, monkeypatch, capsys):
    assert cli.main(args(run) + ["prompt", "manual"]) == 0
    for provider in ("chatgpt", "gemini"):
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(raw()))
        assert cli.main(args(run) + ["answer", "manual", "--round", "1", "--provider", provider]) == 0
    with pytest.raises(ValueError, match="manual_paste"):
        ask(run, object(), 1, opt_in=True)
    assert cli.main(args(run) + ["prompt", "manual", "--round", "2"]) == 0
    import_answer(run, 2, "chatgpt", raw("Beta", "beta.example"))
    monkeypatch.setattr(cli, "HTTPHomepageStage", lambda: HTTPHomepageStage(
        lambda url: {"http_status": 200, "body": "<title>Official product</title>", "final_url": url}))
    assert cli.main(args(run) + ["check", "manual"]) == 0
    table, _ = cli.synthesize(run)
    for row in table["rows"]:
        assert cli.main(args(run) + ["confirm", "manual", row["candidate_id"], "--url", row["cells"]["homepage"]["value"]]) == 0
    fake = Path(__file__).parent / "fixtures/fake_counter.py"
    assert cli.main(args(run) + ["lookup", "manual", "--counter", str(fake)]) == 0
    capsys.readouterr()
    assert cli.main(args(run) + ["status", "manual"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["rounds"][0]["providers"][0]["method"] == "manual_paste"
    assert all(m["method"] == "manual_paste" for mentions in status["mentioned_by"].values() for m in mentions)
    assert cli.main(args(run) + ["render", "manual"]) == 0
    table = json.loads((run / "table.json").read_text())
    assert len(table["rows"]) == 2
    assert all(row["lookup_status"] == "estimate" and row["homepage_status"] == "confirmed" for row in table["rows"])
    assert len(table["rows"][0]["mentioned_by"]) == 2
    assert table["rows"][0]["cells"]["name"]["method"] == "manual_paste"
    for file in ("table.md", "report.html", "evidence.csv"):
        assert "manual_paste" in (run / file).read_text()
    assert "outbound_consent" not in json.loads((run / "request.json").read_text())

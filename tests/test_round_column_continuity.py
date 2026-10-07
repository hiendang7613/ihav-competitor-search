"""Round context must retain typed values without redeclaring old columns."""
import json
from pathlib import Path

import pytest

from ihav_competitor_search.chatbots import WebChatChild, ask, collect, preview
from ihav_competitor_search.cli import synthesize
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.survey import create_run


PRICE = {"key": "price", "label": "Monthly price", "meaning": "monthly price",
         "type": "number", "unit": "USD/month"}


def envelope(name, value, *, provider="chatgpt", columns=()):
    return {"provider": provider, "fetched_at": "2026-10-07T00:00:00Z",
            "raw": {"columns": list(columns), "candidates": [
                {"name": name, "homepage": "https://example.com/" + name,
                 "values": {"price": value}}]}}


def initial():
    return merge_round(empty_table(), [envelope("Alpha", 19, columns=[PRICE])], 1)


@pytest.mark.parametrize("value,expected,reason", [
    (12, 12, None), (0, 0, None),
    (False, None, "type_mismatch"), ("12", None, "type_mismatch"),
])
def test_existing_column_values_from_another_provider(value, expected, reason):
    result = merge_round(initial(), [envelope("Beta", value, provider="gemini")], 2)
    price = result["rows"][1]["cells"]["price"]
    assert price["value"] == expected
    assert price["raw_value"] == value
    assert price["reason"] == reason
    assert price["raw_unit"] == "USD/month"
    assert price["source_url"] == "chatbot:gemini:r2"
    assert price["fetched_at"] == "2026-10-07T00:00:00Z"
    assert price["verification"] == "unverified"
    assert result["rounds"][-1]["new_columns"] == 0


def test_repeat_adds_observation_without_backfilling_old_cell():
    result = merge_round(initial(), [envelope("Alpha", 99, provider="gemini")], 2)
    alpha = result["rows"][0]
    assert len(result["rows"]) == 1
    assert alpha["cells"]["price"]["value"] == 19
    assert alpha["cells"]["price"]["source_url"] == "chatbot:chatgpt:r1"
    assert alpha["observations"][-1]["raw"]["values"]["price"] == 99


def test_redeclared_key_with_different_meaning_keeps_separate_column():
    annual = {**PRICE, "meaning": "annual price", "unit": "EUR/year"}
    result = merge_round(initial(), [envelope("Beta", 240, columns=[annual])], 2)
    beta = result["rows"][1]["cells"]
    assert beta["price"]["value"] is None
    assert beta["price_2"]["value"] == 240
    assert beta["price_2"]["raw_unit"] == "EUR/year"


def test_zero_new_column_budget_still_accepts_existing_column_values():
    result = merge_round(initial(), [envelope("Beta", 12)], 2,
                         max_new_columns=0, max_total_columns=11)
    assert len(result["columns"]) == 11
    assert result["rows"][1]["cells"]["price"]["value"] == 12


def test_new_column_without_a_proposal_is_not_inferred():
    raw = envelope("Beta", 12)
    raw["raw"]["candidates"][0]["values"]["unknown_metric"] = 7
    result = merge_round(initial(), [raw], 2)
    assert "unknown_metric" not in result["rows"][1]["cells"]


def test_later_prompt_explains_existing_value_keys_and_excludes_private_data():
    table = initial()
    table["private_note"] = "DO_NOT_SEND_PRIVATE_NOTE"
    table["rows"][0]["observations"].append({"raw": "DO_NOT_SEND_RAW"})
    prompt = preview({"domain": "OCR", "providers": ["chatgpt", "gemini"]},
                     ["chatgpt", "gemini"], 2, table)["prompt"]
    assert "Use accepted column keys in values without redeclaring those columns." in prompt
    projection = json.loads(prompt.splitlines()[-1])
    assert "price" in projection["columns"]
    assert projection["candidates"][0]["values"]["price"] == 19
    assert "DO_NOT_SEND_PRIVATE_NOTE" not in prompt
    assert "DO_NOT_SEND_RAW" not in prompt


def test_saved_round_synthesis_keeps_values_without_schema_repetition(tmp_path):
    directory = create_run(tmp_path, "OCR", rounds=2)
    import_answer(directory, 1, "chatgpt", json.dumps(envelope("Alpha", 19, columns=[PRICE])["raw"]))
    import_answer(directory, 2, "gemini", json.dumps(envelope("Beta", 12)["raw"]))
    table, _ = synthesize(directory)
    beta = next(r for r in table["rows"] if r["cells"]["name"]["value"] == "Beta")
    assert beta["cells"]["price"]["value"] == 12
    assert beta["cells"]["price"]["method"] == "manual_paste"
    assert beta["cells"]["price"]["source_url"] == "manual_paste:gemini:r2"


def test_two_provider_two_round_child_collection_keeps_values_and_receipts(tmp_path):
    """Exercise actual parent/child commands with the explicitly offline fixture."""
    directory = create_run(tmp_path, "OCR", rounds=2, providers=["chatgpt", "gemini"])
    fake = Path(__file__).parent / "fixtures" / "fake_web_chat.py"
    child = WebChatChild(fake, tmp_path)
    config = tmp_path / "fake-chat-config.json"
    config.write_text(json.dumps({"answers": {
        "chatgpt": json.dumps(envelope("Alpha", 19, columns=[PRICE])["raw"]),
        "gemini": json.dumps(envelope("Bravo", 27, columns=[PRICE])["raw"]),
    }}))
    ask(directory, child, opt_in=True)
    assert collect(directory, child)["parsed_answers"] == 2
    round_one = {p.name: p.read_bytes() for p in (directory / "rounds/1/answers").iterdir()}

    config.write_text(json.dumps({"answers": {
        "chatgpt": json.dumps(envelope("Beta", 12)["raw"]),
        "gemini": json.dumps(envelope("Delta", 15)["raw"]),
    }}))
    saved_table, _ = synthesize(directory)
    ask(directory, child, round_number=2, table=saved_table)
    assert collect(directory, child, round_number=2)["parsed_answers"] == 2
    table, _ = synthesize(directory)
    rows = {r["cells"]["name"]["value"]: r for r in table["rows"]}
    assert rows["Beta"]["cells"]["price"]["value"] == 12
    assert rows["Delta"]["cells"]["price"]["value"] == 15
    assert rows["Beta"]["cells"]["price"]["source_url"] == "chatbot:chatgpt:r2"
    assert rows["Delta"]["cells"]["price"]["source_url"] == "chatbot:gemini:r2"
    assert {p.name: p.read_bytes() for p in (directory / "rounds/1/answers").iterdir()} == round_one
    sends = [json.loads(line) for line in (tmp_path / "chat-sends.jsonl").read_text().splitlines()]
    assert len(sends) == 2
    assert sends[0]["run_id"] != sends[1]["run_id"]
    context = json.loads(sends[1]["prompt"].splitlines()[-1])
    assert {r["name"] for r in context["candidates"]} == {"Alpha", "Bravo"}
    for provider in ("chatgpt", "gemini"):
        record = json.loads((directory / "rounds/2/answers" / (provider + ".json")).read_text())
        assert record["method"] == "web_chat_delivery"
        assert record["child_run_id"] == sends[1]["run_id"]
        assert record["request_key"] == sends[1]["request_key"]
        assert len(record["raw_sha256"]) == 64

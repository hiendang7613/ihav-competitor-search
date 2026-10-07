import copy
import json
import socket

import pytest

from ihav_competitor_search.cli import main, synthesize
from ihav_competitor_search.merge import MAX_JSON_DEPTH, empty_table, merge_round
from ihav_competitor_search.verification import (
    BASIS, MAX_INPUT_BYTES, RAW_BYTES, STORE_NAME, apply_verifications,
    load_evidence, record_cell_evidence,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("verification must not access the network")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def confirmed_table(count=1):
    table = merge_round(empty_table(), [{"provider": "synthetic", "raw": {
        "columns": [], "candidates": [
            {"name": f"Product {number}", "homepage": f"https://product{number}.example/app",
             "values": {"description": "Unverified original"}}
            for number in range(count)],
    }}], 1, max_new=max(count, 1))
    for row in table["rows"]:
        row["homepage_status"] = "confirmed"
        row["verification_status"] = "partial"
        for key in ("homepage", "domain"):
            row["cells"][key]["verification"] = "verified"
    return table


def evidence(table, column="description", value="Corrected from page", row=0, **updates):
    item = table["rows"][row]
    column_type = next(entry["type"] for entry in table["columns"] if entry["key"] == column)
    return {"schema_version": 1, "candidate_id": item["candidate_id"], "column": column,
            "value": value, "type": column_type, "source_url": f"https://{item['lookup_host']}/about",
            "fetched_at": "2026-10-06T00:00:00Z", "method": "page",
            "raw_excerpt": "Synthetic official-page excerpt supplied by the host.", "checked_by": "host",
            **updates}


def test_correction_preserves_original_and_truthful_partial_row(tmp_path):
    table = confirmed_table()
    original = copy.deepcopy(table)
    record = record_cell_evidence(table, tmp_path, evidence(table))
    updated = apply_verifications(table, tmp_path)
    assert table == original
    cell = updated["rows"][0]["cells"]["description"]
    assert cell["value"] == "Corrected from page"
    assert cell["raw_value"] == "Unverified original"
    assert cell["verification"] == "verified"
    assert cell["verification_basis"] == BASIS and cell["checked_by"] == "host"
    assert cell["evidence"] == record
    assert record["original_raw_value"] == "Unverified original"
    assert updated["rows"][0]["verification_status"] == "partial"
    assert updated["verification"]["applied_cells"] == 1


def test_older_candidate_missing_extra_column_can_be_filled(tmp_path):
    table = confirmed_table()
    table = merge_round(table, [{"provider": "synthetic", "raw": {
        "columns": [{"key": "price", "meaning": "monthly price", "type": "number", "unit": "USD"}],
        "candidates": [],
    }}], 2)
    record_cell_evidence(table, tmp_path, evidence(table, "price", 12, unit="USD"))
    updated = apply_verifications(table, tmp_path)
    cell = updated["rows"][0]["cells"]["price"]
    assert cell["value"] == 12 and cell["raw_value"] is None
    assert cell["raw_unit"] is None
    assert cell["evidence"]["evidence"]["unit"] == "USD"


def test_all_filled_cells_checked_does_not_verify_missing_facts(tmp_path):
    table = confirmed_table()
    for column in ("name", "description"):
        value = table["rows"][0]["cells"][column]["value"]
        record_cell_evidence(table, tmp_path, evidence(table, column, value))
    updated = apply_verifications(table, tmp_path)
    row = updated["rows"][0]
    assert row["verification_status"] == "verified"
    assert row["cells"]["founded"]["value"] is None
    assert row["cells"]["founded"]["verification"] == "unverified"


@pytest.mark.parametrize("status", ["partial", "unverified"])
def test_unresolved_check_does_not_correct_given_value(tmp_path, status):
    table = confirmed_table()
    record_cell_evidence(table, tmp_path, evidence(
        table, value="Possible different value", verification=status, reason="Page is ambiguous"))
    cell = apply_verifications(table, tmp_path)["rows"][0]["cells"]["description"]
    assert cell["value"] == "Unverified original"
    assert cell["raw_value"] == "Unverified original"
    assert cell["verification"] == status
    assert cell["checked_value"] == "Possible different value"
    assert cell["reason"] == "Page is ambiguous"


def test_repeat_is_idempotent_and_different_evidence_requires_replace(tmp_path):
    table = confirmed_table()
    first = evidence(table)
    recorded = record_cell_evidence(table, tmp_path, first)
    before = (tmp_path / STORE_NAME).read_bytes()
    assert record_cell_evidence(table, tmp_path, first) == recorded
    assert (tmp_path / STORE_NAME).read_bytes() == before
    with pytest.raises(ValueError, match="already recorded"):
        record_cell_evidence(table, tmp_path, evidence(table, value="Replacement"))
    assert (tmp_path / STORE_NAME).read_bytes() == before
    record_cell_evidence(table, tmp_path, evidence(table, value="Replacement"), replace=True)
    store = json.loads((tmp_path / STORE_NAME).read_text())
    assert len(store["records"]) == 1
    cell = apply_verifications(table, tmp_path)["rows"][0]["cells"]["description"]
    assert cell["value"] == "Replacement" and cell["raw_value"] == "Unverified original"


@pytest.mark.parametrize("updates,match", [
    ({"schema_version": True}, "schema_version"),
    ({"schema_version": 2}, "schema_version"),
    ({"candidate_id": "unknown"}, "candidate_id"),
    ({"column": "unknown"}, "column"),
    ({"type": "number"}, "type or unit"),
    ({"value": 14}, "does not fit"),
    ({"value": None}, "checked value"),
    ({"source_url": "https://other.example/about"}, "lookup_host"),
    ({"source_url": "https://user:secret@product0.example/about"}, "credentials"),
    ({"source_url": "javascript:alert(1)"}, "HTTP"),
    ({"source_url": "https://product0.example/\nabout"}, "control"),
    ({"fetched_at": ""}, "fetched_at"),
    ({"fetched_at": "2026-10-06"}, "timezone"),
    ({"method": "chatbot"}, "method"),
    ({"checked_by": "inferred"}, "checked_by"),
    ({"raw_excerpt": " "}, "raw_excerpt"),
    ({"verification": "partial"}, "needs a reason"),
    ({"reason": "Unresolved contradiction"}, "unresolved reason"),
    ({"unexpected": True}, "unknown fields"),
])
def test_rejected_evidence_preserves_saved_records(tmp_path, updates, match):
    table = confirmed_table()
    record_cell_evidence(table, tmp_path, evidence(table))
    before = (tmp_path / STORE_NAME).read_bytes()
    with pytest.raises(ValueError, match=match):
        record_cell_evidence(table, tmp_path, {**evidence(table), **updates}, replace=True)
    assert (tmp_path / STORE_NAME).read_bytes() == before
    assert not (tmp_path / "ask.lock").exists()


def test_deep_evidence_is_a_known_error_without_changing_store(tmp_path):
    table = confirmed_table()
    record_cell_evidence(table, tmp_path, evidence(table))
    before = (tmp_path / STORE_NAME).read_bytes()
    nested = json.loads('[' * MAX_JSON_DEPTH + '0' + ']' * MAX_JSON_DEPTH)
    rejected = evidence(table, notes=nested)
    with pytest.raises(ValueError, match="64 container levels"):
        record_cell_evidence(table, tmp_path, rejected, replace=True)
    source = tmp_path / "deep-evidence.json"
    source.write_text(json.dumps(rejected))
    source_before = source.read_bytes()
    with pytest.raises(ValueError, match="64 container levels"):
        load_evidence(source)
    assert source.read_bytes() == source_before
    assert (tmp_path / STORE_NAME).read_bytes() == before
    assert not (tmp_path / "ask.lock").exists()


@pytest.mark.parametrize("column,value", [
    ("name", "Renamed product"), ("homepage", "https://product0.example/new"),
    ("domain", "other.example"),
])
def test_identity_corrections_require_existing_workflow(tmp_path, column, value):
    table = confirmed_table()
    with pytest.raises(ValueError, match="identity correction"):
        record_cell_evidence(table, tmp_path, evidence(table, column, value))
    assert not (tmp_path / STORE_NAME).exists()


def test_official_confirmation_is_required(tmp_path):
    table = confirmed_table()
    table["rows"][0]["homepage_status"] = "unconfirmed"
    with pytest.raises(ValueError, match="confirmed official"):
        record_cell_evidence(table, tmp_path, evidence(table))
    assert not (tmp_path / STORE_NAME).exists()


def test_scope_defaults_to_top50_and_rechecks_current_rank(tmp_path):
    table = confirmed_table(51)
    with pytest.raises(ValueError, match="top scope"):
        record_cell_evidence(table, tmp_path, evidence(table, row=50))
    record_cell_evidence(table, tmp_path, evidence(table, row=50), top=51)
    outside = apply_verifications(table, tmp_path)
    assert outside["rows"][50]["cells"]["description"]["value"] == "Unverified original"
    assert outside["verification"]["applied_cells"] == 0
    assert outside["verification"]["top"] == 50
    assert len(outside["verification"]["candidate_ids"]) == 50
    table["rows"].insert(0, table["rows"].pop())
    inside = apply_verifications(table, tmp_path, top=1)
    assert inside["rows"][0]["cells"]["description"]["value"] == "Corrected from page"
    assert inside["rows"][1]["cells"]["description"]["value"] == "Unverified original"


@pytest.mark.parametrize("top", [0, -1, True, 1.5])
def test_invalid_scope_rejected_without_writing(tmp_path, top):
    table = confirmed_table()
    with pytest.raises(ValueError, match="positive integer"):
        record_cell_evidence(table, tmp_path, evidence(table), top=top)
    assert not (tmp_path / STORE_NAME).exists()


@pytest.mark.parametrize("change", ["host", "column", "unconfirmed"])
def test_stale_evidence_remains_saved_and_never_applies(tmp_path, change):
    table = confirmed_table()
    record_cell_evidence(table, tmp_path, evidence(table))
    before = (tmp_path / STORE_NAME).read_bytes()
    if change == "host":
        table["rows"][0]["lookup_host"] = "corrected.example"
    elif change == "unconfirmed":
        table["rows"][0]["homepage_status"] = "unconfirmed"
    else:
        next(column for column in table["columns"] if column["key"] == "description")["meaning"] = "new meaning"
    result = apply_verifications(table, tmp_path)
    assert result["rows"][0]["cells"]["description"]["value"] == "Unverified original"
    assert result["verification"]["applied_cells"] == 0
    assert result["issues"][-1]["reason"] == "stale_cell_verification"
    assert (tmp_path / STORE_NAME).read_bytes() == before


def test_excerpt_byte_bound_rejects_without_truncating(tmp_path):
    table = confirmed_table()
    record_cell_evidence(table, tmp_path, evidence(table, raw_excerpt="é" * (RAW_BYTES // 2)))
    before = (tmp_path / STORE_NAME).read_bytes()
    with pytest.raises(ValueError, match="byte limit"):
        record_cell_evidence(table, tmp_path, evidence(table, raw_excerpt="é" * (RAW_BYTES // 2 + 1)), replace=True)
    assert (tmp_path / STORE_NAME).read_bytes() == before


@pytest.mark.parametrize("raw", [
    '{"schema_version":1,"schema_version":2}',
    '{"schema_version":NaN}', '{"schema_version":1e400}',
])
def test_json_loading_rejects_ambiguous_or_nonfinite_input(tmp_path, raw):
    path = tmp_path / "evidence.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        load_evidence(path)


def test_input_byte_bound_and_valid_local_loading(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence(confirmed_table())))
    result = load_evidence(path)
    assert result["value"] == "Corrected from page" and result["verification"] == "verified"
    path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="byte limit"):
        load_evidence(path)


def test_direct_evidence_payload_obeys_same_input_bound(tmp_path):
    table = confirmed_table()
    with pytest.raises(ValueError, match="byte limit"):
        record_cell_evidence(table, tmp_path, evidence(table, value="x" * MAX_INPUT_BYTES))
    assert not (tmp_path / STORE_NAME).exists()


def test_malformed_saved_column_signature_is_not_treated_as_a_fact(tmp_path):
    table = confirmed_table()
    record_cell_evidence(table, tmp_path, evidence(table))
    path = tmp_path / STORE_NAME
    stored = json.loads(path.read_text())
    stored["records"][0]["column_signature"] = ["description", [], None]
    path.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="column signature"):
        apply_verifications(table, tmp_path)


def test_store_schema_corruption_duplicate_and_symlink_rejected(tmp_path):
    table = confirmed_table()
    path = tmp_path / STORE_NAME
    path.write_text('{"schema_version":99,"records":[]}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="store"):
        apply_verifications(table, tmp_path)
    with pytest.raises(ValueError, match="store"):
        record_cell_evidence(table, tmp_path, evidence(table))
    assert path.read_bytes() == before
    path.unlink()
    record_cell_evidence(table, tmp_path, evidence(table))
    store = json.loads(path.read_text())
    store["records"].append(copy.deepcopy(store["records"][0]))
    path.write_text(json.dumps(store))
    with pytest.raises(ValueError, match="duplicate saved"):
        apply_verifications(table, tmp_path)
    path.unlink()
    elsewhere = tmp_path / "other.json"
    elsewhere.write_text("{}")
    path.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="escapes"):
        apply_verifications(table, tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        record_cell_evidence(table, tmp_path, evidence(table))
    assert elsewhere.read_text() == "{}"


def test_existing_run_lock_is_not_removed_or_overridden(tmp_path):
    table = confirmed_table()
    (tmp_path / "ask.lock").mkdir()
    with pytest.raises(ValueError, match="ask.lock"):
        record_cell_evidence(table, tmp_path, evidence(table))
    assert (tmp_path / "ask.lock").is_dir()
    assert not (tmp_path / STORE_NAME).exists()


def test_cli_records_and_renders_local_host_evidence_without_network(tmp_path):
    run = tmp_path / ".ihav_space/ihav-competitor-search/runs/verified"
    answers = run / "rounds/1/answers"
    answers.mkdir(parents=True)
    (run / "request.json").write_text(json.dumps({"domain": "synthetic fixture"}))
    (answers / "manual.json").write_text(json.dumps({"provider": "manual", "method": "manual_paste", "raw": {
        "columns": [], "candidates": [{"name": "Product 0", "homepage": "https://product0.example/app",
                                       "values": {"description": "Unverified original"}}],
    }}))
    arguments = ["--project", str(tmp_path)]
    candidate_id = synthesize(run)[0]["rows"][0]["candidate_id"]
    assert main(arguments + ["confirm", "verified", candidate_id, "--url", "https://product0.example/app",
                             "--method", "official_search"]) == 0
    table = synthesize(run)[0]
    local = tmp_path / "evidence.json"
    local.write_text(json.dumps(evidence(table)))
    assert main(arguments + ["verify", "verified", "--file", str(local)]) == 0
    assert main(arguments + ["render", "verified"]) == 0
    saved = json.loads((run / "table.json").read_text())
    cell = saved["rows"][0]["cells"]["description"]
    assert cell["value"] == "Corrected from page" and cell["raw_value"] == "Unverified original"
    assert cell["verification_basis"] == BASIS and cell["evidence"]["evidence"]["raw_excerpt"]
    assert saved["verification"]["applied_cells"] == 1
    assert saved["rows"][0]["verification_status"] == "partial"
    before = (run / STORE_NAME).read_bytes()
    local.write_text(json.dumps({**evidence(table), "column": "unknown"}))
    assert main(arguments + ["verify", "verified", "--file", str(local), "--replace"]) == 2
    assert (run / STORE_NAME).read_bytes() == before

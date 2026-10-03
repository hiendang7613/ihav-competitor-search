import csv
import io
import json
import shutil
import socket
from pathlib import Path
import pytest
from ihav_competitor_search.cli import main, synthesize
from ihav_competitor_search.merge import empty_table, merge_round, normalize_url
from ihav_competitor_search.rank import apply_visits
from ihav_competitor_search.render import export

FIXTURE = Path(__file__).parent / "fixtures" / "demo"

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

def answer(candidates=(), columns=(), provider="test"):
    return {"provider":provider,"raw":{"columns":list(columns),"candidates":list(candidates)}}

def column(key="extra", meaning="cost", unit="USD", kind="number"):
    return {"key":key,"label":"Cost","meaning":meaning,"type":kind,"unit":unit}

def candidate(name="Product", url="https://example.com/a", **values):
    return {"name":name,"homepage":url,"values":values}

def test_fixture_order_repeat_and_evidence():
    table, snapshots = synthesize(FIXTURE)
    assert [r["cells"]["name"]["value"] for r in table["rows"]] == ["Alpha","Shared A","Shared B","Ranked","Missing","Blocked","Unqueried"]
    assert [r["lookup_status"] for r in table["rows"]] == ["estimate","estimate","estimate","rank_only","no_data","lookup_failed","not_looked_up"]
    assert len(snapshots) == 2
    alpha = table["rows"][0]
    assert len(alpha["mentioned_by"]) == 2
    assert alpha["cells"]["price"]["value"] == 20
    assert alpha["cells"]["price_2"]["value"] is None
    assert table["rounds"][0]["providers"][1]["status"] == "parse_failed"
    assert all(r["traffic_rank"] is None for r in table["rows"][4:])
    assert all(r["homepage_status"] == "unconfirmed" for r in table["rows"])

def test_columns_semantics_budgets_types_and_stop():
    table = merge_round(empty_table(), [answer([candidate(extra=True)], [column()])], 1)
    c = table["rows"][0]["cells"]["extra"]
    assert (c["reason"], c["raw_value"], c["value"]) == ("type_mismatch",True,None)
    table = merge_round(table,[answer(columns=[column("alias"),column("euro",unit="EUR"),column("annual",meaning="annual"),column("label",kind="text")])],2,max_new_columns=2)
    assert len(table["columns"]) == 13
    assert len(table["columns"][10]["proposed_by"]) == 2
    assert any(i["reason"] == "column_budget" for i in table["issues"])
    assert not table["rounds"][-1]["stop"]
    table = merge_round(table,[answer(columns=[column(meaning="x")])],3,max_total_columns=13)
    assert table["rounds"][-1]["stop"]

def test_identity_preserves_vendor_products_and_ambiguity():
    table = merge_round(empty_table(),[answer([candidate("One"),candidate("Two"),candidate("One","https://example.com/b")])],1)
    assert len(table["rows"]) == 3
    assert any(i["reason"] == "ambiguous_identity" for i in table["issues"])
    table = merge_round(table,[answer([candidate("ONE","https://www.example.com/a/")],provider="other")],2)
    assert len(table["rows"]) == 3
    assert table["rounds"][-1]["stop"]
    assert len(table["rows"][0]["mentioned_by"]) == 2
    assert normalize_url("https://API.example.com/product")[1] == "api.example.com"

def test_sort_numbers_ties_and_failures():
    table = merge_round(empty_table(),[answer([candidate(n,f"https://{n.lower()}.example") for n in "ZABCDEF"])],1)
    visits = {"z.example":{"exit_code":0,"result":{"kind":"estimate","monthly_visits":100}},"a.example":{"exit_code":0,"result":{"kind":"estimate","monthly_visits":100}},"b.example":{"exit_code":0,"result":{"kind":"rank_only","monthly_visits":None,"rank":{"value":10}}},"c.example":{"exit_code":0,"result":{"kind":"rank_only","monthly_visits":None,"rank":{"value":2}}},"d.example":{"exit_code":5},"e.example":{"exit_code":64}}
    assert [r["cells"]["name"]["value"] for r in apply_visits(table,visits)["rows"]] == list("AZCBDEF")
    visits["a.example"]["result"]["monthly_visits"] = True
    with pytest.raises(ValueError): apply_visits(table,visits)

def test_cli_outputs_reproducible_and_status_readonly(tmp_path,capsys):
    directory = tmp_path / ".ihav_space/ihav-competitor-search/runs/demo"
    shutil.copytree(FIXTURE,directory)
    args = ["--project",str(tmp_path)]
    assert main(args+["status","demo"]) == 0
    assert not (directory / "table.json").exists()
    assert main(args+["render","demo"]) == 0
    names = ["table.json","table.csv","evidence.csv","table.md","report.html"]
    first = {n:(directory/n).read_bytes() for n in names}
    assert main(args+["render","demo"]) == 0
    assert first == {n:(directory/n).read_bytes() for n in names}
    table = json.loads(first["table.json"])
    rows = list(csv.DictReader(io.StringIO(first["table.csv"].decode())))
    evidence = list(csv.DictReader(io.StringIO(first["evidence.csv"].decode())))
    assert len(rows) == 7
    assert len(evidence) == len(table["rows"])*len(table["columns"])
    assert evidence[0]["source_url"] == "chatbot:chatgpt:r1"
    assert "not owner analytics" in first["report.html"].decode()
    assert "Warning:" in capsys.readouterr().err
    assert main(args+["render","../escape"]) == 2

def test_untrusted_html_and_verified_links(tmp_path):
    table = apply_visits(merge_round(empty_table(),[answer([candidate("<script>alert(1)</script>|x")])],1),{})
    c = table["rows"][0]["cells"]["name"]
    c.update(verification="verified",source_url="https://example.com/source",method="page",fetched_at="2026-10-03")
    export(table,tmp_path)
    report = (tmp_path/"report.html").read_text()
    assert "<script>" not in report
    assert 'href="https://example.com/source"' in report
    assert "&#124;" in (tmp_path/"table.md").read_text()
    c["source_url"] = "javascript:alert(1)"
    export(table,tmp_path)
    assert 'href="javascript:' not in (tmp_path/"report.html").read_text()

def test_invalid_result_preserves_output(tmp_path):
    directory = tmp_path / ".ihav_space/ihav-competitor-search/runs/demo"
    shutil.copytree(FIXTURE,directory)
    (directory/"table.json").write_text("previous output")
    (directory/"visits.json").write_text('{"alpha.example":{"exit_code":0,"result":{"kind":"estimate","monthly_visits":"invented"}}}')
    assert main(["--project",str(tmp_path),"render","demo"]) == 2
    assert (directory/"table.json").read_text() == "previous output"

def test_fenced_answers_reserved_keys_and_bad_json_numbers():
    raw = json.dumps({"columns":[column("traffic_rank")],"candidates":[candidate(traffic_rank=2)]})
    table = merge_round(empty_table(),[{"provider":"test","raw":"```json\n"+raw+"\n```"}],1)
    assert table["columns"][-1]["key"] == "column_traffic_rank"
    assert table["rows"][0]["cells"]["column_traffic_rank"]["value"] == 2
    table = merge_round(table,[{"provider":"broken","raw":'{"columns":[],"candidates":[],"extra":NaN}'}],2)
    assert table["rounds"][-1]["providers"][0]["status"] == "parse_failed"

def test_conflicting_core_key_does_not_pollute_core_value():
    table = merge_round(empty_table(),[answer([candidate(category="Cloud")],
                         [column("category",meaning="deployment location",unit=None,kind="category")])],1)
    cells = table["rows"][0]["cells"]
    assert cells["category"]["value"] is None
    assert cells["category_2"]["value"] == "Cloud"

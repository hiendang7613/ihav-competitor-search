import json
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request
from email.message import Message

import pytest

from ihav_competitor_search import cli
from ihav_competitor_search.homepages import (HTTPHomepageStage, SameSiteRedirects, RedirectBoundary,
    check_homepages, confirm_homepage, apply_homepage_decisions)
from ihav_competitor_search.merge import empty_table, merge_round
from ihav_competitor_search.rank import apply_visits
from ihav_competitor_search import homepages

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket,"socket",forbidden)
    monkeypatch.setattr(socket,"create_connection",forbidden)

def table(*urls):
    return merge_round(empty_table(),[{"provider":"fake","raw":{"columns":[],"candidates":[
        {"name":f"Product {i}","homepage":url,"values":{}} for i,url in enumerate(urls)]}}],1)

@pytest.mark.parametrize("status,body,final,reason",[
    (200,"<title>Product &amp; Company</title>","https://example.com/a","needs_product_confirmation"),
    (200,"<title>Product</title>","https://www.example.com/new","needs_product_confirmation"),
    (200,"<title>Different site</title>","https://other.example/new","cross_site_redirect"),
    (401,"login","https://example.com/a","blocked"),
    (403,"forbidden","https://example.com/a","blocked"),
    (429,"rate limited","https://example.com/a","blocked"),
    (200,"<title>Just a moment...</title>Verify you are human","https://example.com/a","blocked")])
def test_checked_pages_remain_unconfirmed(status,body,final,reason):
    calls=[]
    def fetch(url):
        calls.append(url)
        return {"http_status":status,"body":body,"final_url":final}
    record=HTTPHomepageStage(fetch).check(table("https://example.com/a")["rows"][0])
    assert len(calls)==1
    assert record["reason"]==reason and record["homepage_status"]=="unconfirmed"
    assert bool(record["eligible_for_confirmation"]) == (reason=="needs_product_confirmation")
    assert record["fetched_at"] and record["final_url"]==final
    if "&amp;" in body:
        assert record["title"]=="Product & Company"

def test_timeout_unknown_and_blocked_hosts_do_not_retry(tmp_path):
    calls=[]
    def fetch(url):
        calls.append(url)
        if "timeout" in url:
            raise TimeoutError()
        return {"http_status":403,"body":"blocked","final_url":url}
    t=table("https://example.com/a","https://example.com/b","https://timeout.example")
    checks=check_homepages(t,tmp_path,HTTPHomepageStage(fetch))
    assert len(calls)==2
    assert checks[t["rows"][1]["candidate_id"]]["state"]=="skipped"
    assert checks[t["rows"][2]["candidate_id"]]["reason"]=="timeout"
    check_homepages(t,tmp_path,HTTPHomepageStage(fetch))
    assert len(calls)==2
    key=t["rows"][0]["candidate_id"]
    (tmp_path/"homepage_checks.json").write_text(json.dumps({key:{"state":"checking","requested_url":"https://example.com/a"}}))
    check_homepages(table("https://example.com/a"),tmp_path,HTTPHomepageStage(fetch))
    assert len(calls)==2

def test_redirect_handler_follows_same_host_only():
    class Response:
        def close(self): pass
    handler=SameSiteRedirects("https://example.com/a")
    req=Request("https://example.com/a")
    redirected=handler.redirect_request(req,Response(),302,"",{},"https://www.example.com/b")
    assert redirected.full_url=="https://www.example.com/b"
    with pytest.raises(RedirectBoundary,match="") as exc:
        handler.redirect_request(req,Response(),302,"",{},"https://other.example/b")
    assert exc.value.reason=="cross_site_redirect"
    with pytest.raises(RedirectBoundary) as exc:
        handler.redirect_request(req,Response(),302,"",{},"http://example.com/b")
    assert exc.value.reason=="unsafe_redirect"

def fixture_run(project):
    run=project/".ihav_space/ihav-competitor-search/runs/homepage"
    folder=run/"rounds/1/answers"
    folder.mkdir(parents=True)
    (run/"request.json").write_text('{"domain":"synthetic"}')
    (folder/"fake.json").write_text(json.dumps({"provider":"fake","raw":{"columns":[],"candidates":[
        {"name":"Product","homepage":"https://old.example/a","values":{}}]}}))
    return run

def test_check_confirm_lookup_render_end_to_end(tmp_path,monkeypatch):
    run=fixture_run(tmp_path)
    args=["--project",str(tmp_path)]
    calls=[]
    def fetch(url):
        calls.append(url)
        return {"http_status":200,"body":"<title>Product</title>","final_url":url}
    monkeypatch.setattr(cli,"HTTPHomepageStage",lambda:HTTPHomepageStage(fetch))
    assert cli.main(args+["check","homepage"])==0
    assert cli.main(args+["check","homepage"])==0
    assert len(calls)==1
    t,_=cli.synthesize(run)
    key=t["rows"][0]["candidate_id"]
    assert cli.main(args+["confirm","homepage",key,"--url","https://old.example/a"])==0
    request=json.loads((run/"request.json").read_text())
    assert request["homepage_confirmations"]["old.example"]["method"]=="page"
    fake=Path(__file__).parent/"fixtures/fake_counter.py"
    assert cli.main(args+["lookup","homepage","--counter",str(fake)])==0
    assert cli.main(args+["render","homepage"])==0
    row=json.loads((run/"table.json").read_text())["rows"][0]
    assert row["homepage_status"]=="confirmed" and row["lookup_status"]=="estimate"
    assert row["cells"]["homepage"]["verification"]=="verified"
    assert cli.main(args+["confirm","homepage",key,"--unconfirmed","not this product"])==0
    request=json.loads((run/"request.json").read_text())
    assert "old.example" not in request["homepage_confirmations"]
    row=cli.synthesize(run)[0]["rows"][0]
    assert row["lookup_status"]=="not_looked_up" and row["homepage_status"]=="unconfirmed"

def test_official_search_corrects_host_without_changing_identity(tmp_path):
    run=fixture_run(tmp_path)
    t,_=cli.synthesize(run)
    key=t["rows"][0]["candidate_id"]
    with pytest.raises(ValueError,match="eligible"):
        confirm_homepage(t,run,key,"https://old.example/a")
    confirm_homepage(t,run,key,"https://correct.example/product",method="official_search")
    row=cli.synthesize(run)[0]["rows"][0]
    assert row["lookup_host"]=="correct.example"
    assert row["cells"]["homepage"]["value"]=="https://correct.example/product"
    assert row["candidate_id"]==key and row["cells"]["homepage"]["raw_value"]=="https://old.example/a"
    with pytest.raises(ValueError,match="already recorded"):
        confirm_homepage(t,run,key,"https://another.example",method="official_search")

def test_shared_host_does_not_rank_unconfirmed_product(tmp_path):
    t=table("https://shared.example/a","https://shared.example/b")
    (tmp_path/"request.json").write_text("{}")
    checks=check_homepages(t,tmp_path,HTTPHomepageStage(lambda url:{"http_status":200,"body":"<title>Product</title>","final_url":url}))
    key=t["rows"][0]["candidate_id"]
    confirm_homepage(t,tmp_path,key,"https://shared.example/a")
    request=json.loads((tmp_path/"request.json").read_text())
    applied=apply_homepage_decisions(t,request,tmp_path)
    from_table={"shared.example":{"exit_code":0,"result":{"kind":"estimate","monthly_visits":100}}}
    rows=apply_visits(applied,from_table)["rows"]
    assert rows[0]["homepage_status"]=="confirmed" and rows[0]["shared_domain"]
    assert rows[1]["homepage_status"]=="unconfirmed" and rows[1]["lookup_status"]=="not_looked_up"

def test_fetcher_uses_bounded_plain_get(monkeypatch):
    calls=[]
    class Response:
        code=200
        headers=Message()
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def geturl(self): return "https://example.com"
        def read(self,count):
            assert count==homepages.MAX_BYTES+1
            return b"<title>Product</title>"
    class Opener:
        def open(self,request,timeout):
            calls.append((request,timeout))
            return Response()
    monkeypatch.setattr(homepages,"build_opener",lambda handler:Opener())
    page=homepages.fetch_page("https://example.com")
    request,timeout=calls[0]
    assert len(calls)==1 and timeout==10
    assert request.get_method()=="GET"
    assert "ihav-competitor-search" in request.get_header("User-agent")
    assert request.get_header("Cookie") is None
    assert page["http_status"]==200 and page["body"]=="<title>Product</title>"

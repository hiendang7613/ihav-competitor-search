"""One bounded homepage check, followed by explicit host-agent confirmation."""
import copy
import gzip
import io
import json
import re
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .merge import normalize_url
from .render import atomic_write

MAX_BYTES = 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.svg_depth = 0
        self.current = None
        self.current_in_head = False
        self.head_title = None
        self.fallback_title = None
        self.head_seen = False
        self.fallback_seen = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "head":
            self.in_head = True
        elif tag == "svg":
            self.svg_depth += 1
        elif tag == "title" and not self.svg_depth and self.current is None:
            self.current = []
            self.current_in_head = self.in_head

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title" and self.current is not None and not self.svg_depth:
            title = " ".join("".join(self.current).split())[:1000] or None
            if self.current_in_head and not self.head_seen:
                self.head_title = title
                self.head_seen = True
            elif not self.current_in_head and not self.fallback_seen:
                self.fallback_title = title
                self.fallback_seen = True
            self.current = None
        elif tag == "head":
            self.in_head = False
        elif tag == "svg":
            self.svg_depth = max(0, self.svg_depth - 1)

    def handle_data(self, data):
        if self.current is not None and not self.svg_depth:
            self.current.append(data)


def page_title(body):
    parser = TitleParser()
    parser.feed(body)
    return parser.head_title if parser.head_seen else parser.fallback_title


def decode_content(data, content_encoding):
    """Bound both wire bytes and expanded bytes; never decode unknown formats as HTML."""
    if len(data) > MAX_BYTES:
        return b"", "wire_body_limit", True
    if content_encoding in {"", "identity"}:
        return data, None, False
    if content_encoding not in {"gzip", "deflate"}:
        return b"", "unsupported_content_encoding", False
    try:
        if content_encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                expanded = stream.read(MAX_BYTES + 1)
        else:
            try:
                decoder = zlib.decompressobj()
                expanded = decoder.decompress(data, MAX_BYTES + 1)
            except zlib.error:
                # Some HTTP servers use raw DEFLATE rather than the zlib wrapper.
                decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                expanded = decoder.decompress(data, MAX_BYTES + 1)
            if len(expanded) <= MAX_BYTES and (not decoder.eof or decoder.unused_data):
                return b"", "invalid_content_encoding", False
        if len(expanded) > MAX_BYTES:
            return b"", "decoded_body_limit", True
        return expanded, None, False
    except (OSError, EOFError, zlib.error):
        return b"", "invalid_content_encoding", False


class RedirectBoundary(Exception):
    def __init__(self, url, status, reason):
        self.url, self.status, self.reason = url, status, reason


class SameSiteRedirects(HTTPRedirectHandler):
    def __init__(self, initial):
        self.initial = initial
        self.host = normalize_url(initial)[1]
        self.redirects = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        try:
            _, host = normalize_url(target)
        except ValueError:
            fp.close()
            raise RedirectBoundary(target, code, "unsafe_redirect") from None
        if host != self.host:
            fp.close()
            raise RedirectBoundary(target, code, "cross_site_redirect")
        if urlsplit(req.full_url).scheme == "https" and urlsplit(target).scheme != "https":
            fp.close()
            raise RedirectBoundary(target, code, "unsafe_redirect")
        if len(self.redirects) >= 3:
            fp.close()
            raise RedirectBoundary(target, code, "redirect_limit")
        self.redirects.append({"from": req.full_url, "to": target, "status": code})
        return super().redirect_request(req, fp, code, msg, headers, target)


def fetch_page(url):
    """No cookies, retries, browser or credential handling. Same-host redirects
    are bounded to three; cross-site redirects are recorded without fetching.
    """
    normalize_url(url)
    redirects = SameSiteRedirects(url)
    opener = build_opener(redirects)
    request = Request(url, headers={"User-Agent": "ihav-competitor-search/0.1 homepage-check", "Accept": "text/html"})
    try:
        response = opener.open(request, timeout=10)
    except RedirectBoundary as exc:
        return {"final_url": url, "redirect_target": exc.url, "http_status": exc.status,
                "body": "", "reason": exc.reason, "redirects": redirects.redirects}
    except HTTPError as exc:
        response = exc
    with response:
        data = response.read(MAX_BYTES + 1)
        content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
        data, reason, truncated = decode_content(data, content_encoding)
        encoding = response.headers.get_content_charset() or "utf-8"
        try:
            body = data.decode(encoding, errors="replace")
        except LookupError:
            body = data.decode("utf-8", errors="replace")
        return {"final_url": response.geturl(), "http_status": response.code, "body": body,
                "content_encoding": content_encoding or "identity", "reason": reason,
                "truncated": truncated, "redirects": redirects.redirects,
                "challenge_header": response.headers.get("cf-mitigated", "").lower() == "challenge"}


class HTTPHomepageStage:
    def __init__(self, fetcher=fetch_page):
        self.fetcher = fetcher

    def check(self, candidate):
        url = candidate["cells"]["homepage"]["value"]
        record = {"candidate_id": candidate["candidate_id"], "requested_url": url,
                  "fetched_at": now(), "method": "page", "homepage_status": "unconfirmed"}
        try:
            page = self.fetcher(url)
            body = page.get("body", "")
            status = page.get("http_status")
            final = page.get("final_url", url)
            _, requested_host = normalize_url(url)
            _, final_host = normalize_url(final)
            challenge = bool(page.get("challenge_header") or re.search(r"cf-chl-|cdn-cgi/challenge-platform|verify you are human|checking your browser|captcha challenge|just a moment(?:\.\.\.|…)", body, re.I))
            reason = page.get("reason")
            if requested_host != final_host:
                reason = "cross_site_redirect"
            elif status in {401, 403, 429} or challenge:
                reason = "blocked"
            elif reason is None and (not isinstance(status, int) or not 200 <= status < 300):
                reason = "http_error"
            record.update({k: v for k, v in page.items() if k != "body"})
            record.update(title=page_title(body), final_url=final, http_status=status,
                          state="checked", reason=reason or "needs_product_confirmation",
                          eligible_for_confirmation=reason is None)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            timeout = isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError))
            record.update(state="checked", reason="timeout" if timeout else "fetch_failed",
                          detail=type(exc).__name__, eligible_for_confirmation=False)
        return record


@contextmanager
def homepage_lock(directory):
    path = directory / "homepage.lock"
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError("Homepage lock exists; reconcile the previous writer before removing it.") from None
    try:
        yield
    finally:
        path.rmdir()


def read_checks(directory):
    path = directory / "homepage_checks.json"
    return json.loads(path.read_text()) if path.exists() else {}


def check_homepages(table, directory, stage):
    with homepage_lock(directory):
        checks = read_checks(directory)
        def save():
            atomic_write(directory / "homepage_checks.json", json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
        blocked_hosts = {normalize_url(r["requested_url"])[1] for r in checks.values() if r.get("reason") == "blocked"}
        for row in table["rows"]:
            key = row["candidate_id"]
            if key in checks or row.get("homepage_status") == "confirmed":
                continue
            host = row["lookup_host"]
            if host in blocked_hosts:
                checks[key] = {"candidate_id": key, "state": "skipped", "reason": "blocked",
                               "requested_url": row["cells"]["homepage"]["value"], "fetched_at": now(),
                               "homepage_status": "unconfirmed", "eligible_for_confirmation": False}
                continue
            checks[key] = {"state": "checking", "requested_url": row["cells"]["homepage"]["value"], "fetched_at": now()}
            save()
            checks[key] = stage.check(row)
            save()
            if checks[key].get("reason") == "blocked":
                blocked_hosts.add(host)
        # A saved checking intent is not dispatched again on resume.
        for record in checks.values():
            if record.get("state") == "checking":
                record.update(state="unknown", reason="check_outcome_unknown", homepage_status="unconfirmed")
        save()
        return checks


def confirm_homepage(table, directory, candidate_id, url=None, *, reason=None, method="page"):
    if method not in {"page", "official_search"}:
        raise ValueError("confirmation method must be page or official_search")
    row = next((r for r in table["rows"] if r["candidate_id"] == candidate_id), None)
    if row is None:
        raise ValueError("unknown candidate_id")
    if (url is None) == (reason is None):
        raise ValueError("provide one URL or an unconfirmed reason")
    if reason is not None and not reason.strip():
        raise ValueError("unconfirmed reason must not be empty")
    with homepage_lock(directory):
        request_path = directory / "request.json"
        request = json.loads(request_path.read_text())
        decisions = request.setdefault("homepage_decisions", {})
        previous = decisions.get(candidate_id, {})
        if method == "official_search" and previous.get("search_used"):
            raise ValueError("official-site search is already recorded for this candidate; do not search again")
        date = now()
        record = {"candidate_id": candidate_id, "status": "confirmed" if url else "unconfirmed",
                  "source_url": url, "fetched_at": date, "method": method, "reason": reason,
                  "previous_lookup_host": row["lookup_host"],
                  "search_used": previous.get("search_used", False) or method == "official_search"}
        if url:
            _, host = normalize_url(url)
            checks = read_checks(directory)
            if method == "page":
                check = checks.get(candidate_id, {})
                if not check.get("eligible_for_confirmation"):
                    raise ValueError("no eligible saved page check; use one official-site search or record unconfirmed")
                if normalize_url(check["final_url"])[0] != normalize_url(url)[0]:
                    raise ValueError("confirmation URL differs from the checked final URL")
            record["lookup_host"] = host
        decisions[candidate_id] = record
        # Rebuild host confirmations from candidate decisions. Removing one
        # product's confirmation does not remove another product on that host.
        old_hosts = {r.get("lookup_host") for r in decisions.values()} | {previous.get("lookup_host")}
        confirmations = request.setdefault("homepage_confirmations", {})
        for host in old_hosts:
            confirmations.pop(host, None)
        for decision in decisions.values():
            if decision.get("status") == "confirmed":
                confirmations[decision["lookup_host"]] = copy.deepcopy(decision)
        atomic_write(request_path, json.dumps(request, ensure_ascii=False, indent=2) + "\n")
        return record


def apply_homepage_decisions(table, request, directory):
    table = copy.deepcopy(table)
    checks = read_checks(directory)
    decisions = request.get("homepage_decisions", {})
    for row in table["rows"]:
        row["homepage_decision_required"] = "homepage_decisions" in request or bool(checks)
        decision = decisions.get(row["candidate_id"])
        if decision:
            row["homepage_status"] = decision["status"]
            row["homepage_reason"] = decision.get("reason")
            if decision["status"] == "confirmed":
                row["verification_status"] = "partial"
                row["lookup_host"] = decision["lookup_host"]
                cell = row["cells"]["homepage"]
                cell.update(value=decision["source_url"], source_url=decision["source_url"], fetched_at=decision["fetched_at"],
                            method=decision["method"], verification="verified", reason=None)
                row["cells"]["domain"].update(value=decision["lookup_host"], source_url=decision["source_url"],
                                              fetched_at=decision["fetched_at"], method=decision["method"], verification="verified", reason=None)
        elif row["candidate_id"] in checks:
            row["homepage_reason"] = checks[row["candidate_id"]].get("reason")
        row["homepage_check"] = checks.get(row["candidate_id"])
    return table

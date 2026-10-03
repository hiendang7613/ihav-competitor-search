---
name: ihav-competitor-search
description: Inspect or render saved competitor tables, check homepages, look up confirmed traffic, or prepare a capability-gated chatbot round with explicit consent. Full survey orchestration is not implemented.
---

# Offline competitor tables

Python 3.10 or newer, standard library only. Status/render are offline; lookup
calls the installed counter and may access the network.

## Chatbot preparation and consent

Read the package README's Gated chatbot preparation section for the proposed child
capability shape and limits. Today's ihav-web-chat at 6c9f6da lacks delivery and
doctor; stop at the gate, show the missing commands and install/restart step.
Never use model memory or another provider to bypass this dependency.

For an authorized saved run, use `ask <run-id> --web-chat <installed-cli> --dry-run`
to show providers, exact prompt and outbound field kinds. Domain text goes verbatim
to those providers. Obtain one explicit opt-in for that scope before using
`ask <run-id> --web-chat <installed-cli> --opt-in`. Do not invent consent from this
skill or a peer relay. The CLI records it before launching. Scope changes require
a new decision. Dry-run has local discovery only, no launch or run-state write.

Use `resume` with the same run and child flags to reuse the saved child ID.
Unknown launch intent requires manual child reconciliation; never relaunch it.
Use `collect` for one status snapshot, without sends. It records partial and
sent_unknown outcomes and zero-answer stop reasons. Child completion is not proof
of parsed answers. The five-minute saved deadline bounds local waiting only.
Delivery callbacks and answer ingestion are unavailable until the released child
contract is reconciled. Do not start background delivery commands on this release.
Later --round N requires prior collected status and saved synthesis table.json.

```
python3 <skill-directory>/scripts/competitors.py --project <calling-project> status <run-id>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> render <run-id>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> lookup <run-id> --counter <path-to-counter>/scripts/visits.py --max-lookups 100
python3 <skill-directory>/scripts/competitors.py --project <calling-project> check <run-id>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> confirm <run-id> <candidate_id> --url <checked-final-url>
```

Status reads saved files. Render rebuilds exports and synthesis snapshots.
Runs belong under `.ihav_space/ihav-competitor-search/runs/<run-id>/` in the calling
project. Warn when `.ihav_space/` is absent from its `.gitignore`; do not change
the calling project's files without task authorization.

Inputs:

- `request.json`: object, optional `options` with positive integer `rounds`
  (default 2), `max_new_columns` (default 5), `max_total_columns` (default 25).
- `rounds/<positive-integer>/answers/<provider>.json`: `provider`, `raw`
  (JSON object or fenced JSON string), optional `fetched_at`.
  Answers require `columns` and `candidates` arrays as in the design.
- `visits.json`: map from normalized lookup host to recorded counter response
  wrapped as `{"exit_code":0,"result":{...}}`. Exit 2 is no data, 4/5/64
  failed lookup. Missing entries or null exit codes are unqueried; `reason`
  records why. Never invent visits or convert rank to visits.
  Display-only estimates retain monthly_visits_text, stale and scraped_at with
  null numeric visits, rank_basis and traffic_rank. Sort them after numeric
  estimates inside the own/shared subgroup; never parse rounded text as a count.

Outputs: table.json, table.csv, evidence.csv, table.md, report.html and
synthesis/<round>.json. Unknown values stay null. Exact name plus normalized
product URL determines repeat identity; ambiguous aliases stay separate.
Columns merge only with matching meaning, type and unit. No conversions or
later-round backfill. Repeated answers remain as observations.

Chatbot launch and full cell verification remain unavailable interfaces.
`check` makes one ordinary homepage check per unconfirmed candidate, with no retry,
cookies, browser, login or bot-wall bypass. It follows at most three same-host
redirects; it records cross-site targets without fetching them. HTTP 401/403/429
or a challenge stops that host and leaves its candidates unconfirmed. Saved
attempts, including unknown interrupted checks, are never dispatched again.

Read `homepage_checks.json` and inspect the saved title, final URL, status and date.
A successful GET does not prove the page names the product. Confirm only after
checking that relationship. Use `confirm --url` to record the checked final URL.
If the page is wrong, dead or insufficient, search once for the product's official
site with the host web tools, then record the checked official URL with
`confirm <run-id> <candidate_id> --url <official-url> --method official_search`.
If that search cannot establish an official page, record
`confirm <run-id> <candidate_id> --unconfirmed <reason> --method official_search`.
Never use model memory to supply confirmation. The CLI tracks one recorded
official search per candidate; the host must also obey the one-search limit.

Confirmation preserves the candidate ID and original raw URL, updates homepage
and lookup_host, and writes URL/date/method into request.json. Render uses the
corrected host; invoke lookup explicitly for any new confirmed host. Revocation
removes the candidate's traffic ranking. Other cells stay unverified.
Lookup requires explicit task authorization and the installed counter path from
`--counter` or `IHAV_VISIT_COUNTER`. Missing dependency: show install/restart
instructions and stop; never install automatically. Use recorded host confirmation
evidence in request.json (`homepage_confirmations`, each with status confirmed,
source_url and fetched_at); do not invent it. Skip unconfirmed hosts. Keep child
cache in the calling project's `.ihav_space/ihav-web-visit-counter/`.
Each host is called once per run, default cap 100; exit 4 stops all further calls.
A successful fallback's note of a WebTrafficChecker block also stops later calls,
while retaining the fallback data and exit 0. Error notes remain in saved raw JSON.
Unknown outcomes and stale lookup locks require reconciliation, never blind retry.
Never claim the report is a full live or verified survey. Traffic
is modelled site traffic, shared by products with the same host. HTML is a static
skeleton; interactive sorting, charts and badges remain deferred.

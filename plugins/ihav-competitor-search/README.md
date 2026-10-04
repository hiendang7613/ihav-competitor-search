# ihav-competitor-search — core and counter adapter

This milestone merges recorded chatbot answers, ranks recorded site traffic and
exports a table with per-cell evidence. An explicit `lookup` command calls the
installed visit-counter dependency. It does not run a full competitor survey.
The package uses Python 3.10+ and the standard library only.

## Gated chatbot preparation

The installed ihav-web-chat at commit `6c9f6da` exposes run, status and providers,
but lacks delivery and versioned capabilities. This milestone therefore refuses
live launches against that child. No real child or provider call was made in tests.
It prepares saved runs; it does not create or complete a full survey automatically.

For a saved run containing domain text in request.json:

```
python3 <skill-directory>/scripts/competitors.py --project <calling-project> ask <run-id> --web-chat <installed-cli> --dry-run
python3 <skill-directory>/scripts/competitors.py --project <calling-project> ask <run-id> --web-chat <installed-cli> --opt-in
python3 <skill-directory>/scripts/competitors.py --project <calling-project> resume <run-id> --web-chat <installed-cli>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> collect <run-id> --web-chat <installed-cli>
```

Use `IHAV_WEB_CHAT` instead of --web-chat if needed. The gate reads --help, then
requires doctor --json with a version string, a commands list containing run,
status, delivery wait and delivery read, and a nonempty providers list. This JSON
shape is a proposed consumer contract, tested only with a fake child. It must be
reconciled with the released child before live use. Missing commands produce the
exact missing names and an install/restart instruction; installing today's release
does not supply unreleased commands. No automatic installation or memory fallback.

The preview prints selected providers, exact prompt and outbound field kinds.
Domain text is sent verbatim. --dry-run performs local capability discovery but
writes no run state and launches nothing. --opt-in records one consent in
request.json before any child run command. Consent covers the selected providers,
domain and candidate name/homepage/accepted-column values across configured rounds.
Changes to that scope stop and require a new explicit decision.

Each round saves prompt.md and launch.json before dispatch. The request key binds
run ID, round and prompt hash. A missing child ID after launch intent becomes
unknown on resume and requires manual reconciliation; it never causes a second
launch. A saved child ID is reused. The ask.lock excludes another ask-stage writer;
use one writer per run across all commands and reconcile crash locks manually.

collect reads one status snapshot, preserves every selected provider outcome in
children.json and never sends. Terminal states are completed, failed, timeout,
login_required, human_verification_required, not_sent, sent_unknown and cancelled.
Rounds can be waiting, completed, partial or partial_unresolved. A five-minute
saved deadline ends local waiting without rewriting child statuses or cancelling
the child. Zero completed providers stop round 1; later zero-answer rounds record
verification as the next stage. Completed counts remain child status claims,
not evidence that usable answers were delivered or merged.

Later rounds use --round N and require the prior collected round plus saved
table.json. Outbound synthesis contains only names, homepages and accepted column
values; raw observations, request notes and evidence are excluded. Status outcomes
appear in rendered JSON and HTML. Delivery waiting/reading, answer ingestion and
automatic orchestration remain deferred until their released schema is known.

From the repository root:

```
python3 plugins/ihav-competitor-search/core/ihav-competitor-search/scripts/competitors.py status demo
python3 plugins/ihav-competitor-search/core/ihav-competitor-search/scripts/competitors.py render demo
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

First copy `tests/fixtures/demo` into
`.ihav_space/ihav-competitor-search/runs/demo`. The fixture values are synthetic,
not real survey results. Use `--project <path>` before the subcommand when calling
from elsewhere. A missing or invalid saved run returns exit 2; successful status
or render returns 0. Diagnostics go to stderr and command summaries are JSON.

See `core/ihav-competitor-search/SKILL.md` for the input contract. Rendering replaces
the five derived outputs and synthesis snapshots from saved inputs. Each file is
replaced atomically; the entire export set is not a transactional snapshot. Do not
run simultaneous render writers for one run. Invalid inputs are checked before
replacing outputs. `status` is read-only and reports whether a table exists; it
does not certify that outputs match inputs edited since the last render.

## Evidence and ranking

Markdown and HTML show the analysis date beside numeric visits. Display-only
estimates show the scrape date and stale marker beside their formatted value.
One report line appears when the available estimate dates span more than 14
calendar days. Missing or invalid dates are not invented or included in that span.
These view annotations do not change table.json, table.csv or evidence.csv.

- Product identity uses exact normalized name plus product URL. Host alone never
  merges products. Fuzzy aliases and protocol/path changes remain separate.
- Extra columns merge only on matching meaning, type and unit. Currency conversion
  and semantic interpretation are deferred to the host. Type mismatches preserve
  raw data and show a null normalized value with a reason.
- Per-round and total column budgets are enforced. The stop rule requires no new
  candidate and no accepted column. Later rounds do not backfill existing rows;
  their repeat observations and provider mentions remain in JSON.
- Five sort groups: estimate, rank_only, no_data, lookup_failed, not_looked_up.
  Own-host candidates precede shared-host candidates within each group; estimates
  then sort descending and ranks ascending. Ties use name and stable candidate ID.
  Unknown/failed/unqueried rows have no traffic rank.
- Recorded traffic is a modelled site estimate, not owner analytics or product
  traffic. Shared products retain a shared-domain flag. Source and analysis date
  are displayed separately. Rank-only results never become visit counts.
- Display-only estimates retain `monthly_visits_text`, `stale` and `scraped_at`.
  Their numeric visits, ranking basis and traffic rank remain null. They sort
  after numeric estimates within their own/shared subgroup. Rounded text is
  never parsed into a number; scrape dates are not reporting months.
- `table.json` retains all cells and observations; `evidence.csv` keys evidence by
  candidate and column. CSV contains clean values plus status fields. Markdown
  and HTML link cells marked verified to HTTP(S) evidence. Source presence alone
  never marks a cell verified. This core leaves all synthesized cells unverified.

## Remaining integration gates

Chatbot launch and full cell verification raise `NotImplementedError`
behind interfaces. Status and render never call the counter or fetch pages. Missing traffic
stays unqueried. The HTML is a static skeleton; interactive
sort controls, charts and badges are deferred.

The design uses only ihav-web-visit-counter for traffic. Its current source's terms
include a restriction on substantially similar or competing services, with no
numeric usage limit. Admin approved proceeding with a stated risk and stop on
block. That decision does not settle source permission. Future live integration
respects the counter's cache and stops after exit 4.

## Explicit counter lookup

```
python3 plugins/ihav-competitor-search/core/ihav-competitor-search/scripts/competitors.py --project <calling-project> lookup <run-id> --counter <installed-counter>/scripts/visits.py --max-lookups 100
```

The path can instead come from `IHAV_VISIT_COUNTER`. A missing counter stops with
install/restart instructions; this plugin does not install it. The command may
access the network through the child; run it only when that task is authorized.
No real counter call has been made for this adapter's checks.

The child runs with the calling project as cwd and an explicit cache directory
at `.ihav_space/ihav-web-visit-counter/`. The adapter passes arguments directly,
without a shell. Results, raw JSON/stdout/stderr and exit codes are saved per host
in `visits.json`; render consumes that file offline. Completed lookups are never
retried in the run. The cap counts historical dispatched hosts, including those
removed from later synthesis. Skipped hosts can be attempted later if the cap is
raised or confirmation is supplied, unless a block or unresolved call exists.

For counter contract_version >= 2, a providers entry with
name webtrafficchecker and outcome blocked stops further lookups. A cached
outcome never triggers a stop by itself; historical notes are ignored for v2.
For older counters, exit 4 or fallback notes reporting a WebTrafficChecker block
still stop the run. Both rules retain prior results and
marking unqueried hosts `visits_unavailable: blocked`. Timeout/interruption saves
an unknown outcome and stops dispatch; reconcile it manually instead of retrying.
A `lookup.lock` directory excludes another writer. A lock left by a crash requires
checking that the previous process stopped before manual removal; saved launching
records still prevent redispatch after removing the lock. Each child has a
120-second timeout and no automatic retry.

`request.json` supplies explicit
`homepage_confirmations` keyed by lookup host, with `status: confirmed`,
`source_url` and `fetched_at`. Unconfirmed hosts are skipped. This is host-supplied
page evidence; the plugin never invents confirmations or treats a chatbot URL as
checked. A saved traffic result alone does not verify any candidate cell.

## Homepage check and host-agent confirmation

```
python3 <skill-directory>/scripts/competitors.py --project <calling-project> check <run-id>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> confirm <run-id> <candidate_id> --url <checked-final-url>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> confirm <run-id> <candidate_id> --url <official-url> --method official_search
python3 <skill-directory>/scripts/competitors.py --project <calling-project> confirm <run-id> <candidate_id> --unconfirmed <reason>
```

`check` may access the network and needs task authorization. This implementation
was checked with injected fetchers only; no live run was made. Each candidate gets
one initial GET with a descriptive User-Agent, ten-second timeout and a one-MiB
body limit. At most three redirects on the same normalized host are followed;
cross-site redirects and HTTPS downgrades are recorded without fetching their
targets. No retries, cookies, browser, login or bot-wall bypass are used.

`homepage_checks.json` retains final URL, status, title, date and failure reason.
For each response received by check, raw/<candidate_id>.headers.json records the
status, final response URL, date and filtered response headers; the paired .body
contains at most the first 64 KiB of wire bytes, before decompression. Cookie,
authorization, authentication, token and API-key headers are removed. A blocked
response or refused redirect keeps its available evidence without fetching the
redirect target. No response body is invented for transport failures. Raw bytes
stay in the run folder and never enter the JSON table or check output. Each file
is replaced atomically; the evidence pair is not a transactional snapshot.
Title extraction prefers the first document title in head and ignores SVG titles.
Responses marked gzip or deflate are decoded with the standard library before
HTML parsing. Both wire bytes and decoded bytes are capped at 1 MiB. Unsupported
content encodings, corrupt compressed bodies and size-limit failures remain
unconfirmed with an explicit reason; the checker never retries them.
HTTP 401/403/429 or recognized challenge markers stop that host's remaining
candidates. Other hosts can continue. A saved check is not repeated, including
timeout and interrupted intent. A crash can leave `homepage.lock`; reconcile the
previous process before manually removing it. Use one writer per run across
check, confirm, lookup and render commands.

A successful GET does not confirm a product. The host agent checks that the page
names it and runs `confirm`. If the page is wrong or insufficient, the agent may
search the official site once with its web tools, then record the official URL
using `--method official_search`. If no official page is established, it records
`--unconfirmed` with a reason. CLI records track one official search per candidate;
the host also owns the one-search limit. Never fill a confirmation from memory.

Confirmations are local writes, with no fetch. Candidate IDs and raw chatbot URLs
stay stable while the displayed homepage and lookup host update. Confirm writes
URL, date, method and candidate decisions to request.json. Lookup consumes its
host confirmations; unconfirmed products stay unqueried even when another product
on the same host has traffic. Render uses corrected hosts but makes no lookup.
Revoking confirmation removes the row's traffic rank. Homepage/domain evidence
does not verify the rest of a candidate's cells.

The manifests follow the existing ihav sibling layout. Host installation and
discovery have not been exercised. No install, provider call or publication is
part of this milestone.

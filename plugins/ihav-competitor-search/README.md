# ihav-competitor-search — core and counter adapter

This milestone merges recorded chatbot answers, ranks recorded site traffic and
exports a table with per-cell evidence. An explicit `lookup` command calls the
installed visit-counter dependency. It does not run a full competitor survey.
The package uses Python 3.10+ and the standard library only.

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

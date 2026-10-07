# ihav-competitor-search — core and counter adapter

See the [repository overview](https://github.com/hiendang7613/ihav-competitor-search#readme) for installation prerequisites,
the synthetic demo, accuracy, terms and privacy. This page is the detailed
command and saved-input reference.

This milestone merges recorded chatbot answers, ranks recorded site traffic and
exports a table with per-cell evidence. An explicit `lookup` command calls the
installed visit-counter dependency. Bounded survey steps integrate the installed
WebChat child; homepage/traffic/page checks remain explicit host actions.
The package uses Python 3.10+ and the standard library only.

The primary product requirement is multi-chatbot search/research through
ihav-web-chat, with successive rounds extending accepted results and comparison
dimensions. Actual provider execution and observed search/research settings are
required evidence. Manual imports, advertised adapters, queue receipts and local
tests do not establish completion of this primary feature. Search/research-mode
selection and a live multi-provider, multi-round run remain unqualified.

## Manual copy/paste survey

Manual mode works without ihav-web-chat. Start with a saved run at
`.ihav_space/ihav-competitor-search/runs/<run-id>/` containing `request.json`:

```json
{"domain":"your domain text", "options":{"rounds":2,"max_new_columns":5}}
```

Run this local walkthrough with the shared script:

```
python3 <skill-directory>/scripts/competitors.py --project <calling-project> prompt <run-id>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> answer <run-id> --round 1 --provider chatgpt --file <pasted-answer.txt>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> answer <run-id> --round 1 --provider gemini --file <another-answer.txt>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> render <run-id>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> prompt <run-id> --round 2
python3 <skill-directory>/scripts/competitors.py --project <calling-project> answer <run-id> --round 2 --provider chatgpt --file <round-two-answer.txt>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> render <run-id>
```

Copy the printed prompt into each chatbot yourself and save its JSON answer as
UTF-8 text. The plugin sends nothing and does not create an opt-in record.
`prompt` prints only the exact prompt, without writing state or discovering a
child. Later prompts rebuild the current table from prior recorded rounds rather
than stale exports; they ask for only new candidates and up to the configured
new-column budget. Provider selection is your choice in manual mode.

New candidates can use previously accepted column keys in `values` without
redeclaring those columns. Accepted types and units still apply. Explicitly
different column meanings/types/units remain separate, and repeated candidates
retain their original cells while adding the new raw observation.

`answer` accepts plain or fenced JSON with `columns` and `candidates` arrays.
Omit --file, or use --file -, to read stdin. It saves the exact raw text, provider,
date and method `manual_paste` in rounds/<n>/answers/<provider>.json. Invalid text
is retained with status `parse_failed` and returns exit 2; valid input returns 0.
Candidate/column issues still use the existing conservative merge rules.
Answers, counter replies and cell evidence allow at most 64 nested JSON object
or array levels, counting the root container. Deeper inputs become explicit
errors before ranking or rendering; rejected answer/counter raw text is retained.
Provider names allow letters, digits, dots, underscores and hyphens, starting
with a letter or digit, up to 128 characters. Duplicate round/provider imports
are refused unless --replace; replacement overwrites that saved answer, so keep
your source text if you need the older version. Re-render after imports/replacements.
Do not mix manual imports and automated launches in one round.
Round N requires a consecutive, usable predecessor chain. After a later round
records answers, a prompt or child intent, earlier answers cannot be replaced or
extended. Preserve the original run and use a new run for a corrected scope.
Synthesis binds provider names to answer filenames and validates saved method,
status and child identity. Legacy envelopes without status remain supported.

Legacy saved object answers remain supported. If a rejected object cannot be
written as strict UTF-8 JSON, the failure table retains ASCII-escaped JSON text
with `raw_representation: ascii_escaped_json_text`. The original answer file
stays unchanged. Unsupported in-memory objects report an unavailable raw
representation explicitly; no rejected input becomes accepted facts.

Status, round outcomes, mentioned_by and cell evidence label pasted answers as
`manual_paste`; this records how the answer entered the plugin, not proof that
the named provider produced it. The existing check, explicit confirm, lookup and
render commands then work as described below. Check and lookup can access the
network and still require their own task authorization. Other cells remain
unverified; manual import does not make chatbot claims true.

## Gated chatbot preparation

The gate reads local help and `doctor --json`; required commands are `run`,
`run lookup`, `status`, `delivery wait`, and `delivery read`. Capabilities must
name the runtime version, supported providers and absolute `default_state_dir`.
New dispatch also requires `contracts.run_lookup_admission: 1` and
`contracts.launch_outcome: 1`. A child missing either contract stops before new
dispatch, regardless of its version. Requested providers must appear in the
current advertised inventory. `--providers all` selects that full inventory;
otherwise select supported names explicitly. The default ChatGPT/Gemini/Perplexity
selection fails if any requested provider is unsupported. Review the exact
preview before opting in, especially when the inventory grows after an upgrade.
Wrapper version and runtime version are separate. Gate success proves API
availability, not login, browser readiness, a send or a captured response.

For an existing saved run:

```
python3 <skill-directory>/scripts/competitors.py --project <calling-project> ask <run-id> --web-chat <installed-cli> --dry-run
python3 <skill-directory>/scripts/competitors.py --project <calling-project> ask <run-id> --web-chat <installed-cli> --opt-in
python3 <skill-directory>/scripts/competitors.py --project <calling-project> resume <run-id> --web-chat <installed-cli>
python3 <skill-directory>/scripts/competitors.py --project <calling-project> collect <run-id> --web-chat <installed-cli>
```

`IHAV_WEB_CHAT` can supply the child path. Missing capabilities stop with install
and restart instructions. There is no automatic installation or model-memory fallback.
Preview shows exact prompt, providers and outbound field kinds. Domain text goes
verbatim; later rounds send only candidate names, homepages and accepted values.
Raw observations, private notes and evidence are excluded. One explicit opt-in
records exactly that scope before queuing. Changed scope requires a new decision.
Dry-run performs local discovery and creates no run-state or queue writes.

Before dispatch, each round records a stable request key and prompt. Resume uses
the saved prompt/key, not a newly reconstructed launch. A lost response triggers
read-only request-key lookup. Only one exact fixed-path schema-1 child request
matching run ID, prompt, key and providers can resolve it, together with version-1
admission proof for exact durable provider jobs and their prompt digests.
The lookup state directory must match the saved launch state directory.
A matching request folder alone cannot prove queue admission. Zero, multiple, corrupt,
mismatched or unavailable matches remain unknown; no automatic second launch.
Child IDs use the released UTC timestamp plus eight lowercase hex format.
Run and answer paths must remain contained; payload-supplied file paths are ignored.
Current capabilities gate new launches. Collection validates the immutable saved
scope and requires only status/inline delivery, plus lookup when the child ID is
missing. Added or removed providers do not redefine an existing run.
Only a versioned structured `refused/pre_admission/none` result records a known
refusal. It returns the refusal code and a `new_run` action for corrected scope;
it never replays that launch. Bare exit codes and uncertain effects stay unknown.
`duplicate_request_key` instead reconciles the existing keyed request through
the saved state and admission proof; it never recommends a new run while unknown.
Dry-run and consent previews need local capability discovery, not dispatch contracts.
Canonical saved answer dates require ISO timestamps with timezone; the explicit
legacy record format retains its compatibility rules.

`collect` reads one status snapshot and inline completed delivery text. Every
provider outcome stays in `children.json`. Completed text up to 1 MiB is saved
under the round with provider, child ID, request key, SHA-256 and collection date.
Identical reads preserve the answer; changed bytes/identity fail without overwrite.
Malformed JSON stays raw and `parse_failed`; missing/oversized text is unreadable.
`completed_answers` counts child status claims; `parsed_answers` counts usable
inputs. The saved text is the CLI's UTF-8 text, not claimed original wire bytes.
Previously terminal rounds are read again to collect missing answers.

`sent_unknown` remains unresolved and never advances to another send.
Active child states remain waiting after the local deadline; no child status is
rewritten or cancelled. Zero usable round-1 answers stop; later zero-answer rounds
move to verification. Delivery refusal preserves existing answers and reports its
failure. Later prompts rebuild prior-round synthesis, never stale `table.json`.

## Bounded survey and page evidence

`init "domain" --run-id <fresh-id>` creates a validated request locally.
It refuses an existing ID. Existing manually written requests remain supported.
`survey` can create and queue a run, or continue one saved step:

```
python3 <skill-directory>/scripts/competitors.py --project <calling-project> survey "your domain" --providers all --web-chat <installed-cli> --dry-run
python3 <skill-directory>/scripts/competitors.py --project <calling-project> survey "your domain" --providers all --web-chat <installed-cli> --opt-in
python3 <skill-directory>/scripts/competitors.py --project <calling-project> survey --run-id <returned-id> --web-chat <installed-cli>
```

New-run dry-run writes nothing. Without opt-in, a new survey returns the exact
preview and `consent_required`, exit 2, without creating a run. Each continuation
queues at most one new round or collects one delivery snapshot; no browser worker,
homepage GET, traffic lookup or fact fetch starts implicitly.
`homepage_confirmation_pending`, `traffic_lookup_pending`, `awaiting_child`,
`unknown`, `unresolved_send` and `verification_pending` describe unfinished work.
Complete only the explicit next step within the user's authority. Rendering a
partial table remains available offline. `--no-verify` yields
`completed_unverified`, never a verified survey.

After actually checking an official page, submit one local JSON cell check:

```json
{"schema_version":1,"candidate_id":"<id from status>","column":"description","value":"Checked description","type":"text","source_url":"https://example.com/about","fetched_at":"2026-10-05T12:00:00Z","method":"page","raw_excerpt":"Supporting excerpt from the checked page","checked_by":"host"}
```

This schema example is synthetic. Replace every fact and page/date with checked
evidence; a URL alone does not verify a fact. Run
`verify <run-id> --file <evidence.json>`, then `render`.
Required fields match the example; optional fields are unit, verification,
reason and notes. Verification defaults to verified; partial/unverified requires
a reason and never changes the current value. Type/unit must match the accepted
column and source URL must match the confirmed official host. Timestamp needs a
timezone. UTF-8 input is capped at 512 KiB and excerpt at 64 KiB.

Default scope is the top 50 ranked rows (`init/survey --verify-top` changes it).
Verified correction/fill preserves raw_value/raw_unit. Repeating identical evidence
is idempotent; conflicting evidence needs `--replace`. Homepage/domain/name identity
corrections stay in the confirmation workflow. Changed host/column evidence stays
saved but is marked stale and unapplied. The schema-1 `cell_verifications.json`
store uses the existing run lock and atomic writes. One writer per run; reconcile
a crash lock before removing it.

`host_supplied_page_evidence` records explicit host/human attestation. The core
validates types and attribution, not page truth or interpretation. Row verified
means all filled cells were checked; missing cells stay individually unverified.
Saved checks outside the current ranked scope are not applied. Homepage correction
requires explicit new-host lookup and rerender; it does not silently rerun checks.

`render` produces table.json, table.csv, evidence.csv, table.md, report.html and
per-round synthesis snapshots from saved inputs. Each output is atomic; the full
set is not transactional. HTML has sorting, filtering, status badges and bars for
numeric estimates only. Sorting affects display order, not recorded traffic_rank.
No-data, display-only estimates and Tranco ranks never become numeric visit bars.

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
- `--max-new` limits newly accepted candidates per provider per round; excess
  candidates remain in raw answers and `candidate_budget` issues. Repeated existing
  candidates can still add mentions after the limit. Per-round and total column
  budgets are enforced. The stop rule requires no new
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
  never marks a cell verified. Unchecked synthesized cells remain unverified.

## Remaining integration gates

A terminal explicit unconfirmed-homepage decision remains flagged and can finish
with completed_unverified and its candidate IDs. Confirmed unchecked rows still
return verification_pending; this terminal state does not invent verified facts.

Status and render never call the counter or fetch pages. Missing traffic stays
unqueried. Actual native skill loading, browser delivery and live-provider capture
need separate evidence; passing offline fixtures does not establish them.

Traffic comes only through ihav-web-visit-counter. Its primary provider,
WebTrafficChecker, has a restriction on substantially similar or competing
services and no documented numeric usage limit. That clause may apply to this
plugin. Caching and stopping on blocks do not settle source permission. Read the
[public source-term summary](https://github.com/hiendang7613/ihav-competitor-search#data-sources-and-terms) before use.
The adapter uses the counter's cache and stops on a fresh primary-provider block.

## Explicit counter lookup

```
python3 plugins/ihav-competitor-search/core/ihav-competitor-search/scripts/competitors.py --project <calling-project> lookup <run-id> --counter <installed-counter>/scripts/visits.py --max-lookups 100
```

The path can instead come from `IHAV_VISIT_COUNTER`. A missing counter stops with
install/restart instructions; this plugin does not install it. The command may
access the network through the child; run it only when that task is authorized.
The adapter tests use a fake counter; their results do not qualify live providers.

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

`check` may access the network and needs task authorization. Its regression tests
use injected fetchers and do not qualify live websites. Each candidate gets
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

The manifests follow the ihav sibling layout. Package installation/CLI checks,
fresh native session loading, live delivery and publication remain separate outcomes.

---
name: ihav-competitor-search
description: Create and continue bounded competitor surveys, import manual answers, confirm official homepages, rank traffic, record page-backed cell checks and render evidence.
---

# Competitor surveys with evidence

Use Python 3.10+ and the bundled `scripts/competitors.py`, passing
`--project <calling-project>` before the subcommand. Read the package README
at `../../README.md` for the current command/input contracts and source limits.
Run files belong to the calling project's
`.ihav_space/ihav-competitor-search/runs/<run-id>/`. Warn if that runtime area
is missing from its .gitignore; do not edit unrelated project files.

## Local and manual workflow

`init "<domain>" --run-id <fresh-id>` creates a request without child calls.
`prompt <run-id> --round N` prints exact copyable text without state changes.
A person chooses which chatbot to use. `answer <run-id> --round N --provider P
--file F` stores plain/fenced UTF-8 JSON; omitted file or - reads stdin.
Invalid input is retained with exact raw text and parse_failed, exit 2.
Duplicate provider/round imports need explicit --replace, which replaces that
answer; preserve its original source text when history matters.
Keep manual_paste provenance: a typed provider name is attribution, not proof of
authorship. Manual import never authorizes sends, network lookup or fact verification.
Never mix manual and child-launched answers in one round.
Later prompts rebuild prior recorded answers, not stale table.json.
Imports require consecutive usable rounds. After later answers, a prompt or child
intent exists, earlier answers are frozen; use a new run for corrected scope.
The saved --max-new budget limits new candidates per provider per round; excess
raw candidates remain recorded as candidate_budget issues.

## Child queue, recovery and collection

Discover the installed child through --web-chat or IHAV_WEB_CHAT.
`doctor --json` must provide version, supported providers and commands run,
run lookup, status, delivery wait and delivery read. Missing capability stops
with install/restart instructions; no automatic install or memory fallback.
New dispatch requires contracts.run_lookup_admission=1, contracts.launch_outcome=1
and an absolute default_state_dir. Stop before dispatch if those contracts are
missing, regardless of the child's version. Select supported names from its current
inventory; --providers all selects every advertised provider. Preview that exact
selection before opt-in, including newly added providers. The default
ChatGPT/Gemini/Perplexity selection fails explicitly if any requested provider is
unsupported; never change it silently.

`survey "<domain>" --providers all --web-chat <CLI> --dry-run` shows exact prompt,
providers and outbound fields without saving or queuing. Domain goes verbatim.
One explicit opt-in covers selected providers, domain, names/homepages and accepted
values across configured rounds. Do not infer consent from this skill or peers.
After that decision, --opt-in can queue the new run. Continue with
`survey --run-id <id> --web-chat <CLI>`. Each call queues at most one new round
or collects one snapshot; it never starts a browser worker or silently fetches facts.
Use the returned next action for explicit homepage checks, confirmations, traffic
lookup or evidence submission within existing task authorization.

For existing runs, ask --dry-run previews; ask --opt-in queues; resume reuses the
saved prompt/key and exact child identity; collect reads status and inline text.
Unknown launch intent requires unique exact request-key lookup plus version-1
proof of durable provider jobs, matching prompt digests and saved state directory.
A request folder alone is insufficient. Existing child collection uses the saved
provider scope across upgrades and requires only the commands needed for collection.
A structured refused/pre_admission/none result records launch_refused and new_run;
retain it and start a corrected run explicitly. Bare exit codes stay unknown.
For duplicate_request_key, reconcile the original keyed effect through saved-state
admission lookup; keep an unproven result unknown and never advise another run.
Dry-run/consent previews do not require dispatch contracts or write run state.
Missing, multiple, mismatched or unreadable requests remain unknown; never relaunch.
Never resend sent_unknown, select another session or bypass a child refusal.
A local deadline does not rewrite active child status or justify a new send.
completed_answers counts status claims; parsed_answers counts usable JSON.
Delivery raw text, SHA-256, date and child/provider identity remain immutable;
changed delivery fails for reconciliation rather than overwriting.
No output-supplied response path is opened. A queue, gate or parsed mock is not a
verified live send. Do not start delivery/browser workers without their own authority.

For research dispatch, read `../../references/research-contract.md`.
`init`/`survey --research-targets-file FILE` accepts a versioned exact target set;
it is exclusive with --providers and requires at least two research providers and
at least two budgeted rounds. Select only exact advertised descriptors, including
their scalar setting types. Empty real-provider target arrays stop dispatch.
Preview lists targets and the round/generation budgets. The explicit decision is
persisted with the trusted host session ingress; generated authorization checks
scope consistency and is not independent human consent or native authority.
Later authorization is derived only from that unchanged persisted decision.
Typed saved launches keep their original prompt, targets and authorization files;
capability drift does not replay or reinterpret them. Typed lookup must also prove
the saved request version, scope, authorization hash and each provider's target hash.
Research qualification requires an exact completed execution receipt tied to the
saved raw answer and persisted provider turn. Missing or changed receipts block
research progression. Status completion, usable JSON and checked factual cells
remain separate labels. Legacy plain-chat and manual input stay supported and
cannot acquire research attribution. The parent queues only; a child's worker send
needs its separate authorization, and native controls remain independent.

## Homepage, ranking and official-page cell checks

`check <id>` makes one bounded ordinary GET per eligible homepage, no cookies,
login, retries or bot-wall bypass. Same-host redirects are limited to three;
cross-site redirects and HTTPS downgrades are not fetched. 401/403/429/challenge
stops that host. Inspect saved diagnostics and page/product relationship.
A successful GET alone does not confirm a product.

Record checked relationship with `confirm <id> <candidate_id> --url <checked-url>`.
If insufficient, search once for the official site using host tools and record
--method official_search; if unresolved, use --unconfirmed <reason>.
A terminal unconfirmed decision can finish as completed_unverified with explicit
candidate IDs; confirmed unchecked rows still require verification.
Never confirm from memory. Preserve candidate identity/raw URL. Changed homepage
requires explicit lookup of the new confirmed host and rerender.
Revocation removes traffic ranking; it does not erase the original observations.

`lookup <id> --counter <installed>/scripts/visits.py --max-lookups 100` calls the
counter, or use IHAV_VISIT_COUNTER. Require network task authorization and recorded
official confirmation. One attempt per host per run; prior attempts count toward
the cap. Fresh provider blocks or unknown calls stop further dispatch; no retries.
Keep the counter's own cache under .ihav_space/ihav-web-visit-counter.
Traffic sources and terms belong to the counter; visits are modelled site estimates,
shared across products on one host. Rank-only and rounded text never become visits.

After actually reading official pages, `verify <id> --file <local-evidence.json>`
records one typed cell check. Use the README's exact schema, official-host URL,
timezone date, supporting excerpt and explicit checked_by host/human.
Default scope top 50 ranked rows; --verify-top sets the initialized scope.
Verified corrections/fills retain original raw values; differing evidence needs
--replace. Partial/unverified checks need a reason and preserve existing facts.
Identity corrections stay in confirmation. Old-host/column records stay saved but
unapplied. Host-supplied attestation is not independent fact verification.
A verified row means all filled cells checked; missing facts remain unverified.

## Outputs and continuity

status is read-only. render rebuilds JSON, CSV, evidence CSV, Markdown, HTML and
synthesis snapshots offline. HTML sorting/filtering affects view only; visit bars
encode numeric estimates, not Tranco or rounded display text.
Keep unverified, unknown, failed and unqueried states visible.
Do not claim market coverage, a full live survey, native loading or publication from
offline tests. One writer per run; reconcile crash locks and unknown effects first.

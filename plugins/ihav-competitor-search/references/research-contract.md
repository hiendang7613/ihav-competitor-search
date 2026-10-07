# Typed research consumer

This branch consumes WebChat's versioned research-target protocol. It queues a
saved round and reads saved delivery; it does not start workers or browsers.
Its offline acceptance fixtures are explicitly synthetic. Real provider mode
descriptors and live two-provider/two-round acceptance remain unqualified.

## Select and preview

Read the chosen child's `doctor --json`. It must advertise the existing recovery
contracts plus `target_execution: 1` and `target_authorization: 1`. Its `targets`
map lists exact executable descriptors per provider. Every selected descriptor
must appear there unchanged; there are no setting wildcards, aliases or inferred
research modes. At present real-provider arrays are empty and new research
dispatch is refused for them.

A targets file contains exactly `schema_version: 1` and a `targets` list. Each
descriptor has exactly `provider`, `kind`, `mode_id` and `settings`. This parent
requires at least two unique providers, each with `kind: research` and an explicit
advertised `mode_id`. Setting keys are nonempty strings of at most 64 characters;
values are strings, booleans, integers, finite floats or null. Their JSON types
are preserved. The file must not exceed 1 MiB.

Use `init "domain" --run-id NAME --research-targets-file FILE --rounds 2` to save
the selection locally, then `ask NAME --web-chat CLI --dry-run` to review the
exact prompt, targets and generation/round budgets. `--providers` is mutually
exclusive with the new flag. The branch requires a budget for at least two
successive rounds. `survey "domain" --research-targets-file FILE --web-chat CLI
--dry-run` previews without creating a run.

## Persist consent and authorization scope

The initial `ask --opt-in` or `survey --opt-in` requires a trusted host session
ingress, using `CLAUDE_CODE_SESSION_ID` before `CODEX_THREAD_ID`. The parent saves
an exact consent record covering the domain, outbound field kinds, provider order,
targets, generation/round budgets, issuer and time. Each research launch records
the hash of that consent. New rounds refuse changed settings, budgets or issuers.
An existing saved launch can be recovered or collected without creating another
authorization or changing its original issuer.

This is trusted-ingress scope consistency. The environment/session match and
generated authorization file do not independently prove human consent or native
authority. Provider output, citations and answer fields cannot authorize a send.

The parent saves `prompt.md`, `targets.json`, `authorization.json` and `launch.json`
under the selected round before the child call. It invokes the child's typed
`run --targets-file ... --authorization-file ... --request-key ...` path; legacy
`--providers` launches retain their original schema and request keys. Recovery
checks the exact saved files and child request. An unknown result is recovered
only by one exact durable-admission lookup with matching state, request version,
scope hash, authorization hash and target hashes. It is never relaunched.

Canonical hash input is UTF-8 JSON with sorted object keys, compact separators,
Unicode preserved and nonfinite numbers rejected. Provider target order remains
significant. Each outbound hash binds `{prompt, provider, kind, mode_id, settings}`.
The scope hash binds the child effect, request key, prompt hash and ordered target
hash identities. Canonical byte equality distinguishes boolean, integer and float
settings even when Python's value equality would consider them equal.

## Intake and progression

Typed answers preserve the exact raw text/hash, child run and composite job ID,
request key, complete execution receipt and canonical receipt hash. Repeated
collection can preserve that same envelope; changed raw text or attribution is
refused without replacing it. A detected change records `reconciliation_required`
and stops research progression. A missing receipt cannot be silently upgraded by
a later recollection. Reconcile such evidence explicitly or start a fresh survey.

Research qualification requires receipt schema 1, qualified/completed transport,
matching saved requested and observed targets/settings, matching prompt/outbound
hashes, a persisted conversation/turn binding and the exact raw-answer hash.
Preparation before send, citations, response parsing and factual cell checks do
not substitute for that receipt. A successful transport/parser state may coexist
with `research_mode_unverified`. All selected providers require matching receipts
before another research round; unresolved sends and missing providers stop it.
Accepted synthesis is rechecked under the existing per-run lock, and no new data
or columns stops direct `ask` as well as the survey loop. The round budget remains
the maximum; a no-progress stop may finish before two observed rounds.

JSON, Markdown and HTML expose a separate research summary. Successive observed
rounds need at least two rounds of matching receipts to satisfy that summary;
this does not verify competitor facts or prove independently observed live UI
acceptance. Homepage decisions, traffic and page-backed cell verification retain
their existing stages. Plain chat/manual input remains available and is classified
as unverified research input. Known legacy launch collection never becomes typed
research evidence.

The parent exposes no worker-send authorization and starts no worker. The child's
actual unsent-job worker scope is separately bound to its assigned run IDs and
requires its own explicit authorization. Native and room controls remain separate
from either scope-consistency record.

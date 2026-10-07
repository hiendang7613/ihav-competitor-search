---
name: ihav-competitor-search
description: Create and continue bounded competitor surveys, import manual answers, rank traffic, record official-page evidence and render tables.
---

# Competitor surveys with evidence

Read `${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/SKILL.md` and the package
README for the shared input, consent, recovery and evidence contracts.
Use the same Python entrypoint; --project comes before the subcommand:

```
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> init "<domain>" --run-id <fresh-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> prompt <run-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> answer <run-id> --round 1 --provider chatgpt --file <answer.txt>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> render <run-id>
```

Manual answers preserve raw input and manual_paste; provider names do not prove
authorship. Never mix manual and child launches in one round. Invalid answers
remain parse_failed; explicit --replace is needed for duplicates.
Rounds require consecutive usable predecessors. Later answers, prompts or child
intent freeze earlier input; use a new run for corrected scope. The saved --max-new
budget limits new candidates per provider per round and preserves excess raw data.
Manual commands do not authorize host sends, homepage GETs or counter lookup.

For automated queue/collection, preview survey "<domain>" --providers all
--web-chat <installed-cli> --dry-run first. Record explicit user consent for
that exact scope before --opt-in. The installed runtime must advertise run lookup,
delivery read/wait and doctor, plus versioned run_lookup_admission=1 and
launch_outcome=1 contracts with default_state_dir. A child missing those contracts
stops before new dispatch, regardless of its version. Select only names in its
current inventory; --providers all selects every advertised provider. Review the
exact preview before opt-in, including providers added by an upgrade. Unsupported
requested providers fail explicitly. Saved child collection preserves
the original provider scope across upgrades; recovery requires exact durable jobs,
prompt hashes and the saved state directory. A request folder alone is insufficient.
Continue survey --run-id <id> with the same child path. One invocation performs
one saved step and never starts a browser worker. Unknown or sent_unknown sends
are reconciled, never repeated. collect stores bounded inline answers with
immutable hashes; completed claims and parsed answer counts remain separate.
Structured refused/pre_admission/none yields launch_refused and new_run; preserve
the original and never replay it. Bare exit codes and uncertain effects stay unknown.
duplicate_request_key requires saved-state reconciliation of the original keyed
request, never another run while unknown. Dry-run/consent previews remain available
without dispatch contracts and create no run-state or queue effects.

Check/lookup require network authority; confirm requires an actually checked
official page/product relationship. One official-site search per candidate,
no login/challenge bypass. Verify records typed official-host cell checks from
a local evidence file after the host actually reads the page; it does not fetch
or infer facts. Follow the shared skill's cap, raw preservation and stale-record
rules. Missing facts and out-of-scope rows remain unverified.
Terminal unconfirmed-homepage decisions can finish as completed_unverified with
explicit candidate IDs; confirmed unchecked rows still need verification.
Run files remain local under .ihav_space/ihav-competitor-search; status/render
are offline. Native skill loading, provider delivery and publication require
their own evidence.

---
name: ihav-competitor-search
description: Inspect or render saved competitor tables, check homepages, look up confirmed traffic, or prepare a capability-gated chatbot round with explicit consent. Full survey orchestration is not implemented.
---

# Offline competitor tables

Read `${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/SKILL.md` for the saved-input
contract and evidence limits. Use the shared Python entry point:

```
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> status <run-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> render <run-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> lookup <run-id> --counter <installed-counter>/scripts/visits.py --max-lookups 100
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> check <run-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> confirm <run-id> <candidate_id> --url <checked-final-url>
```

Lookup calls the dependency and may access the network; require task authorization
and the recorded homepage confirmations described in the shared skill. Check also
accesses the network; no retries or login/bot-wall bypass. A GET never confirms a
product automatically. Follow the shared skill's one official-site search and
explicit confirm/unconfirmed workflow. Full cell verification is unavailable.

For chatbot preparation, follow the shared skill's gate and consent workflow:
`ask <run-id> --web-chat <installed-cli> --dry-run` previews exact fields and prompt;
`--opt-in` may be used only after explicit user consent. Domain text is sent verbatim.
Today's child at 6c9f6da lacks delivery and doctor, so stop with the exact missing
commands and install/restart instruction. No automatic install or memory fallback.
`resume` reuses saved child IDs; unknown launch outcomes require manual reconciliation,
never another send. `collect` records status only. Delivery callbacks and answer
ingestion await the released child schema; do not start a background delivery wait
on this release. A queued status is not delivered-answer evidence.
Keep run files in the calling project's `.ihav_space/ihav-competitor-search/`.

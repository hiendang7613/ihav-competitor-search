---
name: ihav-competitor-search
description: Inspect or render saved competitor tables, or explicitly look up confirmed traffic through the installed visit-counter. Full chatbot survey is not implemented.
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
explicit confirm/unconfirmed workflow. No chatbot calls or full cell verification
are available.
Keep run files in the calling project's `.ihav_space/ihav-competitor-search/`.

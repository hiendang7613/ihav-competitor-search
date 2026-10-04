---
name: ihav-competitor-search
description: Survey competitors with local copy/paste prompts and JSON answers, render saved tables, check homepages, or look up confirmed traffic. Automated chatbot launch remains capability-gated.
---

# Offline competitor tables

Read `${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/SKILL.md` for the saved-input
contract and evidence limits. Use the shared Python entry point:

Manual walkthrough for a saved run with request.json domain text:

```
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> prompt <run-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> answer <run-id> --round 1 --provider chatgpt --file <answer.txt>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> render <run-id>
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> prompt <run-id> --round 2
python3 "${CLAUDE_PLUGIN_ROOT}/core/ihav-competitor-search/scripts/competitors.py" --project <calling-project> answer <run-id> --round 2 --provider chatgpt --file <answer-two.txt>
```

The user copies the printed prompt to chatbots and saves their plain/fenced JSON
answers as UTF-8 text. Repeat answer for other providers, then render again.
Omitted --file or --file - reads stdin. Invalid text is retained as parse_failed
with exit 2. Duplicate round/provider imports require explicit --replace and
overwrite the old answer. Follow the shared skill's manual_paste provenance rules.
The plugin makes no chatbot call and needs no opt-in; host sends, check and lookup
still need their own authorization. Keep manual and launched answers in separate
rounds. Manual import neither verifies provider authorship nor other cells.

Other commands:

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
automated ingestion await the released child schema; do not start a background delivery wait
on this release. A queued status is not delivered-answer evidence.
Keep run files in the calling project's `.ihav_space/ihav-competitor-search/`.

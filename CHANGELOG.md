# Changelog

## 0.2.0 — 2026-10-08

- Create saved runs with `init`; advance a survey with one queue/collection step.
- Require versioned WebChat queue-admission proof and structured no-effect refusals;
  request-only lookup cannot qualify a recovered launch.
- Preserve existing child collection across provider/capability upgrades without resending.
- Enforce saved candidate budgets per provider and retain rejected raw candidates.
- Require consecutive usable manual rounds and freeze their earlier input after later work.
- Validate saved answer filename, method, status and child provenance before synthesis.
- Allow terminal unconfirmed-homepage decisions to finish with explicit unverified IDs.
- Ingest bounded inline delivery text with immutable hashes and separate parsed counts.
- Record typed official-page cell evidence with explicit host/human provenance,
  preserving original values and limiting scope to the configured ranked prefix.
- Rebuild later-round prompts from recorded inputs; retain partial/unknown outcomes.
- Sort/filter the standalone HTML table and show bars for numeric visit estimates.
- Append verification_basis, checked_by and raw_excerpt to evidence.csv; existing
  evidence fields and table.csv stay supported.
- Reject non-finite/deeply nested or invalid UTF-8 answers, malformed column
  types and empty confirmation URLs.
- Persist malformed counter replies as terminal failures with raw output and
  retained primary-block evidence, rather than leaving a known outcome launching.
- Enforce a 64-container JSON nesting limit for answers, counter replies and cell
  evidence before recursive consumers, including Python 3.13's broader decoder.

Offline tests cover these boundaries. Live browser/provider delivery and native
skill execution are not yet qualified.

## 0.1.0 — 2026-10-04

### Added

- Local manual prompts and plain/fenced JSON answer imports, with raw text,
  dates, `manual_paste` provenance and explicit replacement of duplicate imports.
- Conservative candidate/column synthesis and traffic ranking from saved data.
- Explicit bounded homepage checking and human/host-agent confirmation.
- Traffic lookup through ihav-web-visit-counter, with structured provider-block
  handling, per-host saved outcomes and no automatic retries.
- JSON, CSV, Markdown and static HTML exports with evidence and per-row traffic dates.
- Capped raw homepage response evidence with sensitive header removal.
- Offline tests, host skill packages, public documentation and synthetic SVG demos.

### Not implemented

- Automated chatbot delivery and answer ingestion await child capabilities.
- Full cell verification, automatic survey orchestration and interactive charts.

This is the first public source release. It is not a live-provider qualification.

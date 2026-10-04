# How it works today

The manual workflow has two explicit network stages: homepage `check` and counter
`lookup`. The other commands operate on the saved run. There is no full survey
orchestrator or final cell-verification stage.

1. `prompt` uses the shared prompt builder. The domain stays verbatim. Later
   rounds project names, homepages and accepted values from prior saved answers,
   excluding raw observations and internal evidence. The user sends it themselves.
2. `answer` validates plain/fenced JSON and saves raw text, provider, date and
   `manual_paste`. Parse failures remain recorded; duplicates need `--replace`.
3. `render` rebuilds a table from saved answers. Exact normalized name plus product
   URL determines identity. Columns merge only with matching meaning, type and
   unit. Budgeted extra columns and repeat observations remain traceable.
4. `check` saves intent before a bounded homepage GET. Same-host redirects are
   limited to three; cross-site redirects and HTTPS downgrades are refused.
   Response diagnostics and up to 64 KiB of filtered raw evidence remain local.
5. A person or host agent checks that the page names the product, then `confirm`
   records URL/date/method. A successful GET alone never confirms it. One recorded
   official-site search can correct the homepage while preserving candidate identity.
6. `lookup` calls the counter once per eligible confirmed host, default cap 100.
   It saves results and stops on a fresh primary-provider block or unknown outcome.
   A v2 cached result is not a fresh block. No rank or display string becomes visits.
7. `render` applies saved traffic and produces the five exports plus synthesis
   snapshots. Each file is replaced atomically; the full output set is not transactional.

The merge stops reading later rounds when a round adds neither candidates nor
accepted columns. Repeated candidates contribute observations; later rounds do
not backfill existing cells. Use one writer per run and re-render after replacing
answers. The report is evidence about saved inputs, not a guarantee of market coverage.

## Source and test map

- [CLI](../../plugins/ihav-competitor-search/core/ihav-competitor-search/ihav_competitor_search/cli.py)
  owns commands; [manual tests](../../tests/test_manual.py) exercise the fake end-to-end path.
- [Merge](../../plugins/ihav-competitor-search/core/ihav-competitor-search/ihav_competitor_search/merge.py)
  and [ranking](../../plugins/ihav-competitor-search/core/ihav-competitor-search/ihav_competitor_search/rank.py)
  preserve identities, values and missing-data states; [core tests](../../tests/test_core.py) cover them.
- [Homepage tests](../../tests/test_homepages.py), [visit adapter tests](../../tests/test_visits.py)
  and [report tests](../../tests/test_report_dates_and_raw.py) cover the external
  boundaries using offline fixtures.

Read the [root README](../../README.md) for accuracy, source terms and privacy,
or the [command reference](../../plugins/ihav-competitor-search/README.md) for recovery details.

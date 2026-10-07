# Contributing

## Work from the current contract

Use Python 3.10+ and preserve the standard-library core. Read the root README and
[command reference](plugins/ihav-competitor-search/README.md) before changing behavior.
Keep manual provenance separate from automated child outcomes, and unknown values
separate from zero. Preserve raw input, dates, failure reasons and explicit confirmation.

Do not convert popularity ranks or rounded display strings to numeric visits.
Do not merge products solely because they share a host. Include conservative
identity and typed-column counterexamples when changing synthesis or ranking.

## Tests

Use saved or synthetic fixtures. Do not contact live sites, launch real chatbot
children, require credentials or bypass access blocks in tests. The tests patch
network boundaries and use fake child scripts.

From the repository root, with pytest available in your development environment:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
git diff --check
```

See [test_manual.py](tests/test_manual.py) for the full fake workflow,
[test_homepages.py](tests/test_homepages.py) for fetching/confirmation boundaries,
and [test_visits.py](tests/test_visits.py) for counter contracts and recovery.
The offline workflow covers Linux Python 3.10/3.13 and macOS Python 3.13;
hosted results are available only after this workflow is published and executed.

## Pull requests

Describe the observable change, compatibility effect, exact tests and remaining
limits. Separate offline mocks from live/provider evidence. Add focused regression
fixtures for failure and recovery paths, and update command examples when interfaces change.
Do not attach real run folders, raw private pages, prompts with secrets, cookies
or counter logs. Label synthetic data and retain the provenance of sanitized fixtures.

Traffic sources belong in the counter dependency. Changes to that boundary need
its canonical contract and source terms. No stealth, proxy rotation, CAPTCHA
solving, automatic retry of unknown sends or alternative routes around blocks.

## Documentation and assets

Keep public documentation grounded in current commands and tests. Label invented
numbers, names and example domains synthetic; do not present illustrations as
live screenshots. Keep internal coordination records and runtime files out of
public examples. MIT covers the code, not permission to redistribute provider data.

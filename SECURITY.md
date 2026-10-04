# Security policy

## Scope

The current source milestone is 0.1.0, unreleased. No supported published release
or security audit is claimed. Reports about the current source are welcome.

## Reporting a vulnerability

If GitHub private vulnerability reporting is enabled for this repository, use
its Security tab to report privately. If it is unavailable, ask the maintainer
hiendang7613 for a private reporting route without posting exploit details.
Do not publish secrets, private run files, credentials or sensitive page bodies
in a public issue. Include a minimal synthetic reproduction, affected source
version, impact and relevant command. No response-time guarantee is advertised.

## Data and execution boundaries

Manual prompt/import commands are local and do not call chatbot providers.
The homepage check makes bounded ordinary GETs; explicit lookup delegates to
ihav-web-visit-counter. Neither stage needs cookies or account credentials.
Access blocks are not bypassed, and unknown launches/checks are not automatically retried.

Run folders can contain private domain text, raw pasted answers, diagnostics and
page-body prefixes. Sensitive response headers are filtered, but content and
URLs are not a general secret-redaction system. Keep `.ihav_space/` out of Git
and inspect exports before sharing. See [privacy notes](README.md#privacy-and-local-files).

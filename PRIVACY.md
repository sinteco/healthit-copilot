# Privacy Policy

_Last updated: 2026-08-13_

HealthIT Copilot is an open-source Claude Code plugin (MIT license) published
at https://github.com/sinteco/healthit-copilot. This policy describes what the
plugin itself does with data. It does not cover Claude Code or Anthropic's
services, which have their own terms and privacy policies.

## Summary

- The plugin runs **entirely on your machine**. It has no backend, no
  analytics, no telemetry, and no accounts.
- **We collect nothing.** The plugin author receives no data of any kind from
  your use of the plugin.
- One optional feature (`lookup_terminology`) makes an outbound network call —
  and only sends a terminology code, never message content.

## What the plugin processes

The MCP server (`mcp/server.py`) processes the HL7 v2 messages and FHIR
resources you paste or reference in your Claude Code session:

- `parse_hl7v2`, `validate_fhir`, `hl7_to_fhir_skeleton`, and
  `generate_engine_code` run **locally** with no network access. Input and
  output stay in process memory; nothing is written to disk by the plugin.
- `validate_fhir_hapi` runs the HL7 validator **locally** (your own
  `validator_cli.jar` + JRE). The resource is written to a temporary file that
  is deleted when validation completes. Note: the validator itself may download
  IG packages from the FHIR package registry on first use.
- `lookup_terminology` sends **only the terminology code and code-system URL**
  (e.g. `code=718-7`, `system=http://loinc.org`) to a terminology server —
  `https://tx.fhir.org/r4` by default, or the server you set in
  `$HEALTHIT_TX_SERVER`. It never sends message content, patient data, or any
  other field. Use `offline: true` (built-in crosswalk) or point
  `$HEALTHIT_TX_SERVER` at an internal server to avoid external calls entirely.

Anything you type into Claude Code is also processed by Claude itself; that
data flow is governed by Anthropic's privacy policy, not this one.

## Protected Health Information (PHI)

This tool is built for **test and de-identified messages only** — spec work,
mapping design, and code generation. Do **not** use it with production PHI:

- A consumer AI assistant is not an appropriate processing environment for PHI
  under HIPAA or similar regulations.
- Never point `lookup_terminology` at a public terminology server while
  working with real patient data.

The `hl7v2` and `hl7-to-fhir-mapping` skills instruct the assistant to flag
messages that appear to contain real PHI and recommend de-identified samples.

## Data retention

None. The plugin stores no data, keeps no logs, and writes no files (other than
the transient temp file used by the HAPI validator, deleted after each run).

## Third-party services

| Service | When contacted | What is sent |
|---------|----------------|--------------|
| tx.fhir.org (or `$HEALTHIT_TX_SERVER`) | only when `lookup_terminology` is called with a code | code + system URI |
| FHIR package registry | only when `validate_fhir_hapi` downloads IG packages | package name/version |

No other network calls are made by this plugin.

## Changes & contact

Changes to this policy are made via pull requests to this repository — the
git history is the changelog. Questions or concerns: open an issue at
https://github.com/sinteco/healthit-copilot/issues.

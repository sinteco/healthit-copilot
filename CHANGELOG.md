# Changelog

All notable changes to HealthIT Copilot. Marketplace installs update through
version bumps here and in `.claude-plugin/plugin.json`.

## 0.3.0 — 2026-08-13

### Added
- **ADT skeleton builder** — `hl7_to_fhir_skeleton` now dispatches on MSH-9:
  ADT messages map to Patient + Encounter (PV1-2 class via HL7 table 0004 →
  v3-ActCode, PV1-19 visit number, PV1-44/45 period, status
  in-progress/finished).
- **ORM skeleton builder** — ORM/OMG/OML messages map to Patient +
  ServiceRequest (ORC-5 status via table 0038, placer/filler order
  identifiers, ORC-9 authoredOn, OBR-7 occurrenceDateTime).
- **FML target for `generate_engine_code`** — third target `"fml"` emits a
  FHIR Mapping Language StructureMap alongside the Mirth and Rhapsody JS.
- **`expand_valueset` tool** — FHIR `ValueSet/$expand` with optional text
  filter and count; VSAC OIDs auto-route to cts.nlm.nih.gov using
  `$UMLS_API_KEY` (HTTP basic `apikey:KEY`).
- **Bulk regression harness** (`tools/regress.py`) — replay a folder of
  `.hl7` messages, record baseline Bundles, diff on later runs with a
  path-level report; exit 1 on drift, `--update` to accept changes.
- Encounter and ServiceRequest structural rules in `validate_fhir`.
- 16 new tests (59 total): ADT/ORM skeletons, FML output, expand_valueset
  (mocked network + VSAC auth header), regression harness end-to-end.

## 0.2.1 — 2026-08-13

### Changed
- **`generate_engine_code` rewritten as engine-agnostic plain ES5** — the
  previous Mirth output used E4X syntax (`for each`, `msg['PID'][...]`), which
  only runs on classic Rhino-based Mirth Connect and silently fails on newer
  Nashorn/GraalJS-based versions. Both Mirth and Rhapsody targets now share a
  single `mapORU(raw)` core with no E4X and no host-object dependencies; the
  Mirth wrapper maps from `connectorMessage.getRawData()`.

### Added
- Execution-level codegen tests: generated JS is run in Node and its Bundle
  output diffed against `hl7_to_fhir_skeleton` (skipped when Node is absent).
- CI badge in README; `PRIVACY.md` privacy policy.

### Fixed
- Stale module docstring (still described the 3-tool v0.1 server).
- Stale `healthit-copilot/0.1` User-Agent in `lookup_terminology`; version is
  now a single `__version__` constant.

## 0.2.0 — 2026-08-13

### Added
- `generate_engine_code` — Mirth/NextGen Connect and Rhapsody JavaScript
  transformer generation mirroring the skeleton mapping; `/gen-engine-code`
  command.
- `validate_fhir_hapi` — official HL7 validator CLI wrapper with IG package
  support (e.g. US Core); graceful setup instructions when java/jar missing.
- `lookup_terminology` — live `CodeSystem/$lookup` against tx.fhir.org
  (configurable via `$HEALTHIT_TX_SERVER`) plus a built-in 30-analyte
  common-lab LOINC crosswalk for offline use.
- HL7 v2-to-FHIR IG crosswalk reference tables shipped with the mapping skill
  (segment maps, datatype/vocab maps, status tables).
- GitHub Actions CI (Python 3.8 + 3.12); test suite grown to 42 tests.

### Changed
- `parse_hl7v2` decodes HL7 escape sequences (`\F\ \S\ \T\ \R\ \E\`, `\Xdd\`,
  `\.br\`).
- `hl7_to_fhir_skeleton` maps PID-7 birthDate, PID-8 gender, repeating PID-3
  identifiers, OBR-25/OBX-11 status tables, OBR-7/22 effective/issued, OBX-7
  referenceRange, OBX-8 interpretation, OBX-14 effectiveDateTime, NTE notes,
  CE/CWE coded values, and SN comparators; emits proper CodeableConcepts
  (LN → loinc.org) and a valid transaction Bundle (fullUrl, urn:uuid
  references, entry.request).
- `validate_fhir` validates Bundles recursively, including transaction
  `entry.request` checks.

## 0.1.0 — 2026-08-13

Initial release: `parse_hl7v2`, `validate_fhir`, `hl7_to_fhir_skeleton` MCP
tools; `hl7-to-fhir-mapping`, `fhir-r4`, `hl7v2` skills; `/map-hl7-to-fhir`,
`/validate-fhir`, `/diag-sync` commands.

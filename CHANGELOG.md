# Changelog

All notable changes to HealthIT Copilot. Marketplace installs update through
version bumps here and in `.claude-plugin/plugin.json`.

## 0.5.0 — 2026-08-13

### Added
- **SIU (scheduling) skeletons** — `hl7_to_fhir_skeleton` now maps SIU
  messages to Appointment resources: SCH-1/2 placer/filler identifiers,
  SCH-7 reason, SCH-8 appointment type, SCH-11 start/end times, and the
  full HL7 table 0278 → `Appointment.status` crosswalk.
- **MDM (document) skeletons** — MDM messages map to DocumentReference:
  TXA-2 document type, TXA-4 date, TXA-12 identifier, TXA-17 completion
  status → `docStatus`, TXA-19 availability (OB → `superseded`), and
  OBX TX/ST/FT body lines packaged as a base64 `text/plain` attachment.
- **`cda_to_fhir` tool** — map a CDA/CCD XML document to a FHIR Bundle:
  header → Patient, Results/Vital Signs → Observations, Problem List →
  Conditions, Medications → MedicationStatements. LOINC/SNOMED/RxNorm/
  ICD-10-CM OIDs translate to canonical FHIR system URIs; unmapped
  sections are reported in `_gaps`.
- **`fhir_to_hl7v2` tool** — the inverse mapping: generate an HL7 v2
  message (ORU^R01, ADT^A01, or ORM^O01) from a skeleton-shaped FHIR
  Bundle, with proper HL7 escape-sequence re-encoding.
- **`round_trip_check` tool** — mapping-fidelity verifier: runs
  HL7 → FHIR → HL7 → FHIR and produces a recursive diff of the two
  Bundles. All seven in-repo samples round-trip losslessly.
- FHIR validation rules for Appointment, DocumentReference, Condition,
  and MedicationStatement.
- Samples + baselines: `siu_s12_booking.hl7`,
  `mdm_t02_discharge_summary.hl7` (regression harness now replays 7
  messages).
- 24 new tests (92 total), including CCD fixture parsing, escape-sequence
  round-trip survival, and per-sample round-trip assertions.

## 0.4.1 — 2026-08-13

### Fixed
- **Generated engine JS now decodes HL7 escape sequences** — CORE_JS
  previously never applied `\F\ \S\ \T\ \R\ \E\ \Xdd..\ \.br\` decoding, so
  Mirth/Rhapsody output silently diverged from `hl7_to_fhir_skeleton` on any
  message containing escapes. A JS port of `_unescape` is now applied per
  component (names, identifiers, coded values, units, notes, valueString),
  and an escape-bearing message was added to the Node execution-diff corpus
  so this can't regress.
- Module docstring updated for all 8 tools (was missing `expand_valueset`
  and `explain_hl7_field`); `generate_engine_code` docstring now documents
  the `fml` target. A new guard test asserts every registered tool name
  appears in the module docstring — this drift shipped twice.

### Changed
- CI pins Node 20 via `actions/setup-node`, so the JS execution-diff tests
  always run instead of silently skipping when node is absent.

## 0.4.0 — 2026-08-13

### Added
- **`explain_hl7_field` tool** — version-aware HL7 v2 field dictionary
  covering 2.3 → 2.8 for MSH/PID/PV1/ORC/OBR/OBX/NTE: field names,
  datatypes (including the CE→CWE and TS→DTM transitions at 2.7 and the
  MSH-9 message-structure component added in 2.3.1), HL7 tables,
  added/withdrawn version info (e.g. PID-2/PID-4/PID-19 withdrawn in 2.7,
  OBX-23/24/25 added in 2.5.1), and FHIR mapping hints.
- **In-repo sample corpus** — `samples/` (2 ORU, 2 ADT, 1 ORM) with
  recorded `baselines/`; CI now replays the regression harness on every
  push, so mapping drift fails the build.
- 7 new tests (66 total).

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

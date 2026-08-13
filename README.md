<div align="center">

# HealthIT Copilot

**HL7 v2 ↔ FHIR R4 mapping and interface debugging, inside Claude Code.**

[![tests](https://github.com/sinteco/healthit-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/sinteco/healthit-copilot/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/sinteco/healthit-copilot)](https://github.com/sinteco/healthit-copilot/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](mcp/server.py)

</div>

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin for
**healthcare interoperability engineers**. It converts **HL7 v2** messages
(ORU, ADT, ORM) to **FHIR R4** resources, validates FHIR JSON against base
rules and IG profiles, generates integration-engine transformers (Mirth
Connect, Rhapsody, FHIR Mapping Language), and diagnoses interface failures —
all through **deterministic** parse/validate/transform tools, so answers are
grounded in real output instead of guessed field positions or value sets.

## Table of contents

- [Quick start](#quick-start)
- [Features](#features)
- [MCP tools](#mcp-tools)
- [Commands & skills](#commands--skills)
- [Usage](#usage)
- [Optional integrations](#optional-integrations)
- [Testing](#testing)
- [Scope, compliance & privacy](#scope-compliance--privacy)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## Quick start

Requires `python3` on PATH. No pip dependencies.

```
/plugin marketplace add sinteco/healthit-copilot
/plugin install healthit-copilot@sintayehu-health
```

Then paste an HL7 message and ask — for example:

```
/map-hl7-to-fhir MSH|^~\&|LAB|HOSP|EMR|HOSP|20240101120000||ORU^R01|1|P|2.5 ...
```

<details>
<summary>Local development install</summary>

```
git clone https://github.com/sinteco/healthit-copilot
/plugin marketplace add ./healthit-copilot
/plugin install healthit-copilot@sintayehu-health
```

</details>

## Features

- **HL7 v2 → FHIR conversion** (ORU, ADT, ORM, SIU, MDM) with an explicit
  gap report — never a silent guess — targeting R4, R4B, or R5
- **CDA/CCD document mapping**: results, problems, medications, allergies,
  immunizations, and procedures → FHIR resources
- **Round-trip checking**: FHIR → HL7 v2 back-generation and a
  HL7 → FHIR → HL7 → FHIR fidelity diff for interface testing
- **Structural + profile validation** (base R4 rules built in; US Core and
  other IGs via the official HL7 validator)
- **Engine code generation**: Mirth/NextGen Connect JS, Rhapsody JS, and FHIR
  Mapping Language StructureMaps
- **Live terminology**: code lookup and ValueSet expansion against
  tx.fhir.org or VSAC
- **Batch mode**: map and validate a whole directory of messages in one call
- **Interface debugging** workflow for "the feed stopped syncing" incidents
- **Zero dependencies**: the MCP server is a single stdlib-only Python file

## MCP tools

All tools live in [`mcp/server.py`](mcp/server.py) (stdlib-only Python).

| Tool | What it does |
| --- | --- |
| `parse_hl7v2` | Spec-correct HL7 v2 parser: honors MSH-1/MSH-2 encoding characters, splits repetitions/components/subcomponents, decodes escape sequences (`\F\`, `\S\`, `\T\`, `\R\`, `\E\`, `\Xdd\`, `\.br\`) |
| `hl7_to_fhir_skeleton` | HL7 v2 → FHIR transaction Bundle, dispatched on MSH-9. **ORU** → Patient + DiagnosticReport + Observations (statuses, timestamps, reference ranges, interpretations, NTE notes); **ADT** → Patient + Encounter (PV1 class/period/visit number); **ORM/OMG/OML** → Patient + ServiceRequest (ORC status, placer/filler identifiers); **SIU** → Patient + Appointment (SCH times, status, appointment type); **MDM** → Patient + DocumentReference (TXA metadata, OBX body as base64 attachment). Every Bundle ships a `_gaps` report |
| `validate_fhir` | Structural FHIR R4 validation: required elements, status value sets, Observation value[x], recursive transaction-Bundle checks |
| `validate_fhir_hapi` | Full **profile validation** (US Core & other IGs) via the official HL7 validator CLI (needs Java + `validator_cli.jar`; set `$HAPI_VALIDATOR_JAR`) |
| `generate_engine_code` | Emit a **Mirth/NextGen Connect** JS transformer, **Rhapsody** JS mapper, or **FHIR Mapping Language** StructureMap mirroring the reviewed mapping. The JS is plain ES5 (no E4X), so it runs on Rhino **and** Nashorn/GraalJS engines alike |
| `lookup_terminology` | Verify codes live against a terminology server (tx.fhir.org by default; `$HEALTHIT_TX_SERVER` to change), with a built-in common-lab LOINC crosswalk for offline use |
| `expand_valueset` | FHIR `ValueSet/$expand` against any terminology server, with **VSAC** support: pass an OID and your `$UMLS_API_KEY` and it routes to cts.nlm.nih.gov automatically |
| `explain_hl7_field` | Version-aware HL7 v2 field dictionary (2.3 → 2.8) for MSH/PID/PV1/ORC/OBR/OBX/NTE: field names, datatypes (incl. CE→CWE and TS→DTM changes at 2.7), HL7 tables, added/withdrawn versions, and FHIR mapping hints |
| `cda_to_fhir` | **CDA/CCD document** → FHIR Bundle: header → Patient, Results/Vitals → Observations, Problem List → Conditions, Medications → MedicationStatements, Allergies → AllergyIntolerances, Immunizations → Immunizations, Procedures → Procedures, with unmapped sections reported in `_gaps` |
| `fhir_to_hl7v2` | The inverse mapping: generate an HL7 v2 message (ORU/ADT/ORM/SIU/MDM) from a skeleton-shaped FHIR Bundle, for interface testing |
| `round_trip_check` | Mapping-fidelity verifier: HL7 → FHIR → HL7 → FHIR, then a recursive diff of the two Bundles. Empty diff = the mapping is lossless for everything it claims to map |
| `map_directory` | **Batch mode**: map and validate a whole directory in one call (`.hl7`/`.txt` as HL7 v2, `.xml` as CDA/CCD) with per-file results and a summary |

`hl7_to_fhir_skeleton` and `cda_to_fhir` accept a `fhir_version` option (`r4` default, `r4b`, `r5`) — the R5 target applies the breaking renames (`Encounter.class`/`actualPeriod`, `Appointment.reason`, `MedicationStatement.medication`).

## Commands & skills

| Command | Purpose |
| --- | --- |
| `/map-hl7-to-fhir` | Map an HL7 v2 message to a FHIR R4 transaction Bundle with a gap report |
| `/validate-fhir` | Validate a FHIR resource or Bundle and explain each finding |
| `/diag-sync` | Diagnose an HL7 interface / sync failure step by step |
| `/gen-engine-code` | Generate a Mirth, Rhapsody, or FML transformer for a reviewed mapping |

**Skills** — `hl7-to-fhir-mapping` (the core workflow, including condensed
HL7 v2-to-FHIR IG crosswalk tables as reference files), `fhir-r4`, and
`hl7v2`. They auto-activate on relevant questions and route through the
deterministic tools.

## Usage

```
/map-hl7-to-fhir MSH|^~\&|LAB|HOSP|EMR|HOSP|20240101120000||ORU^R01|1|P|2.5 ...
/validate-fhir {"resourceType": "Observation", "status": "finalized", ...}
/diag-sync patient records from the lab feed aren't showing up in the EMR
```

Or just ask naturally — *"why is this ORU message being rejected?"* — and the
skills take over.

## Optional integrations

| Integration | Setup |
| --- | --- |
| **HAPI profile validation** | `curl -Lo ~/.healthit/validator_cli.jar https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar` (needs a JRE). `validate_fhir_hapi` then supports IGs, e.g. `igs: ["hl7.fhir.us.core#6.1.0"]` |
| **Custom terminology server** | Point `$HEALTHIT_TX_SERVER` at your own FHIR terminology server. Never send PHI to a public one |
| **VSAC value sets** | Get a free UMLS API key at [uts.nlm.nih.gov](https://uts.nlm.nih.gov/uts/signup-login), export `UMLS_API_KEY=...`, and `expand_valueset` accepts VSAC OIDs |

## Testing

```
python3 -m unittest discover tests -v
```

CI runs the suite on Python 3.8 and 3.12 for every push and pull request.

### Bulk regression harness

The repo ships [`samples/`](samples/) (ORU, ADT, ORM messages) with recorded
[`baselines/`](baselines/), and CI replays them on every push. To use it on
your own feed, point it at any folder of `.hl7` messages:

```
python3 tools/regress.py samples/ --baseline baselines/   # first run records
python3 tools/regress.py samples/ --baseline baselines/   # later runs diff
```

Exits non-zero on drift; `--update` accepts intentional changes.

## Scope, compliance & privacy

> **Runs locally. Test / de-identified data only. No PHI leaves your machine.**

Built for spec work, mapping design, and code generation with **test or
de-identified messages** — not for processing production PHI in a consumer
tool.

The plugin has **no backend, no telemetry, no analytics, and no accounts**.
The only optional network calls are terminology lookups
(`lookup_terminology`, `expand_valueset`), which send a terminology code —
never message content — to the configured terminology server, and can be
avoided entirely with `offline: true`. Full policy:
[PRIVACY.md](PRIVACY.md).

## Roadmap

1. C-CDA reverse generation: FHIR Bundle → CCD document
2. HL7 v2 ACK generation and error-response tooling
3. X12 270/271 eligibility skeleton support
4. Configurable mapping profiles (site-specific Z-segments)

Shipped so far: see [CHANGELOG.md](CHANGELOG.md).

## Contributing

Issues and pull requests are welcome. Keep the MCP server **stdlib-only**
(no pip dependencies), add tests for any mapping change, and run the suite
plus the regression harness before opening a PR.

## License

MIT — see [LICENSE](LICENSE).

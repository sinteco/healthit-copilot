# HealthIT Copilot — HL7 v2 to FHIR R4 mapping plugin for Claude Code

[![tests](https://github.com/sinteco/healthit-copilot/actions/workflows/tests.yml/badge.svg)](https://github.com/sinteco/healthit-copilot/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](mcp/server.py)

**HealthIT Copilot** is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
plugin for **healthcare interoperability engineers**. It converts **HL7 v2**
messages (ORU^R01, ADT, ORM) to **FHIR R4** resources, validates FHIR JSON, and
diagnoses HL7 interface / integration-engine failures — with **deterministic**
parse/validate/transform tools, so Claude explains grounded in real output
instead of guessing at field positions.

Use it for: HL7 to FHIR conversion, FHIR resource validation, LOINC/UCUM
terminology gap analysis, lab-results (ORU) mapping, EHR/EMR integration
debugging, and interface-engine (Mirth Connect, Rhapsody) development.

## Features

- **MCP tools** (`mcp/server.py` — stdlib-only Python, zero pip dependencies):
  - `parse_hl7v2` — spec-correct HL7 v2 parser: honors MSH-1/MSH-2 encoding
    characters, splits repetitions/components/subcomponents, decodes escape
    sequences (`\F\`, `\S\`, `\T\`, `\R\`, `\E\`, `\Xdd\`, `\.br\`)
  - `validate_fhir` — structural FHIR R4 validation: required elements, status
    value sets, Observation value[x], recursive transaction-Bundle checks
  - `validate_fhir_hapi` — full **profile validation** (US Core & other IGs)
    via the official HL7 validator CLI (needs java + `validator_cli.jar`,
    set `$HAPI_VALIDATOR_JAR`)
  - `hl7_to_fhir_skeleton` — HL7 v2 → FHIR transaction Bundle, dispatched
    on MSH-9: **ORU** → Patient + DiagnosticReport + Observations (statuses,
    timestamps, reference ranges, interpretations, NTE notes), **ADT** →
    Patient + Encounter (PV1 class/period/visit number), **ORM/OMG/OML** →
    Patient + ServiceRequest (ORC status, placer/filler identifiers) — all
    with an explicit `_gaps` report
  - `generate_engine_code` — emit a **Mirth/NextGen Connect** JavaScript
    transformer, **Rhapsody** JavaScript mapper, or a **FHIR Mapping
    Language (FML) StructureMap** mirroring the reviewed mapping. The JS is
    plain ES5 with no E4X, so it runs on classic Rhino-based Mirth
    Connect **and** newer Nashorn/GraalJS-based versions alike
  - `lookup_terminology` — verify codes live against a terminology server
    (**tx.fhir.org** by default, `$HEALTHIT_TX_SERVER` to change) with a
    built-in common-lab LOINC crosswalk for offline use
  - `expand_valueset` — FHIR `ValueSet/$expand` against any tx server, with
    **VSAC** support: pass an OID and your `$UMLS_API_KEY` and it routes to
    cts.nlm.nih.gov automatically
- **Skills**: `hl7-to-fhir-mapping` (the core workflow, now shipping condensed
  **HL7 v2-to-FHIR IG crosswalk tables** as reference files), `fhir-r4`, `hl7v2`
- **Commands**: `/map-hl7-to-fhir`, `/validate-fhir`, `/diag-sync`,
  `/gen-engine-code`

## Installation

Requires `python3` on PATH; no pip dependencies.

### From the marketplace (recommended)

```
/plugin marketplace add sinteco/healthit-copilot
/plugin install healthit-copilot@sintayehu-health
```

### Local development

```
git clone https://github.com/sinteco/healthit-copilot
/plugin marketplace add ./healthit-copilot
/plugin install healthit-copilot@sintayehu-health
```

## Usage examples

```
/map-hl7-to-fhir MSH|^~\&|LAB|HOSP|EMR|HOSP|20240101120000||ORU^R01|1|P|2.5 ...
/validate-fhir {"resourceType": "Observation", "status": "finalized", ...}
/diag-sync patient records from the lab feed aren't showing up in the EMR
```

Or just ask naturally: *"why is this ORU message being rejected?"* — the
skills auto-activate and route through the deterministic tools.

## Testing

```
python3 -m unittest discover tests -v
```

### Bulk regression harness

Replay a folder of HL7 messages and diff the resulting Bundles against a
recorded baseline — run it after every mapping change:

```
python3 tools/regress.py samples/ --baseline baselines/   # 1st run records
python3 tools/regress.py samples/ --baseline baselines/   # later runs diff
```

Exit code 1 on drift; `--update` accepts intentional changes.

## Scope & compliance

Built for **test / de-identified** messages: spec work, mapping, and code
generation. Not for production PHI in a consumer tool.

## Privacy

HealthIT Copilot runs **entirely on your machine** — no backend, no telemetry,
no analytics, no accounts. Nothing you process is collected by the plugin
author. The only optional network call is `lookup_terminology`, which sends
just a terminology code (never message content) to https://tx.fhir.org/r4 —
configurable via `$HEALTHIT_TX_SERVER` or avoidable with `offline: true`.
Do not use production PHI. Full policy: [PRIVACY.md](PRIVACY.md).

## Optional extras

- **Profile validation**: download the HL7 validator once —
  `curl -Lo ~/.healthit/validator_cli.jar https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar`
  (needs a JRE) — and `validate_fhir_hapi` lights up, including US Core via
  `igs: ["hl7.fhir.us.core#6.1.0"]`.
- **Terminology server**: `lookup_terminology` uses https://tx.fhir.org/r4 by
  default; point `$HEALTHIT_TX_SERVER` at your own server. Never send PHI to a
  public terminology server.
- **VSAC value sets**: get a free UMLS API key at
  https://uts.nlm.nih.gov/uts/signup-login, export `UMLS_API_KEY=...`, and
  `expand_valueset` can expand any VSAC value set by OID.

## Roadmap

1. HL7 v2.x version-aware field dictionaries (2.3 → 2.8 differences).
2. CDA/CCD document mapping support.
3. SIU (scheduling) and MDM (documents) skeleton builders.
4. Round-trip check: FHIR Bundle → HL7 v2 back-generation for interface tests.

## Keywords

HL7 v2, FHIR R4, healthcare interoperability, HL7 to FHIR converter, ORU^R01,
ADT, ORM, MCP server, Claude Code plugin, LOINC, UCUM, US Core, Mirth Connect,
Rhapsody, integration engine, EHR, EMR, health IT, medical data mapping,
FHIR validation, HL7 parser.

## License

MIT — see [LICENSE](LICENSE).

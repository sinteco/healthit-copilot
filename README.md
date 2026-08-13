# HealthIT Copilot — HL7 v2 to FHIR R4 mapping plugin for Claude Code

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
  - `hl7_to_fhir_skeleton` — ORU^R01 → FHIR transaction Bundle
    (Patient + DiagnosticReport + Observations) with birthDate/gender,
    OBR-25/OBX-11 status mapping, effective/issued timestamps, reference
    ranges, interpretations, NTE notes, and an explicit `_gaps` report
- **Skills**: `hl7-to-fhir-mapping` (the core workflow), `fhir-r4`, `hl7v2`
- **Commands**: `/map-hl7-to-fhir`, `/validate-fhir`, `/diag-sync`

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

## Scope & compliance

Built for **test / de-identified** messages: spec work, mapping, and code
generation. Not for production PHI in a consumer tool.

## Roadmap

1. Target-engine codegen: emit **Mirth/NextGen Connect** JS transformers and
   **Rhapsody** mappings — meet engineers in their existing workflow.
2. Real profile validation: wrap the **HAPI validator CLI** with IG packages
   (US Core, national profiles) as a `validate_fhir_hapi` tool.
3. Live terminology: `lookup_terminology` against a tx server (tx.fhir.org /
   LOINC / VSAC) instead of pass-through codes.
4. Standard mapping tables: ship the **HL7 v2-to-FHIR IG** crosswalks as
   reference files behind the mapping skill.

## Keywords

HL7 v2, FHIR R4, healthcare interoperability, HL7 to FHIR converter, ORU^R01,
ADT, ORM, MCP server, Claude Code plugin, LOINC, UCUM, US Core, Mirth Connect,
Rhapsody, integration engine, EHR, EMR, health IT, medical data mapping,
FHIR validation, HL7 parser.

## License

MIT — see [LICENSE](LICENSE).

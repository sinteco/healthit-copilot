# HealthIT Copilot

A Claude Code plugin for healthcare interoperability engineers. Maps HL7 v2 to
FHIR R4, validates resources, and diagnoses interface failures — with
**deterministic** parse/validate/transform tools so Claude explains grounded in
real output instead of guessing at field positions.

## What's inside

- **MCP tools** (`mcp/server.py`, stdlib-only Python):
  - `parse_hl7v2` — spec-correct HL7 v2 parse, honors MSH encoding chars
  - `validate_fhir` — structural R4 validation (required elements, status value
    sets, Observation value[x])
  - `hl7_to_fhir_skeleton` — ORU^R01 -> FHIR transaction Bundle skeleton
- **Skills**: `hl7-to-fhir-mapping` (the core workflow), `fhir-r4`, `hl7v2`
- **Commands**: `/map-hl7-to-fhir`, `/validate-fhir`, `/diag-sync`

## Install (local dev)

Add to a marketplace or point Claude Code at the plugin directory. Requires
`python3` on PATH; no pip dependencies.

## Scope & compliance

Built for **test / de-identified** messages: spec work, mapping, and code
generation. Not for production PHI in a consumer tool.

## Roadmap (where the moat is)

1. Target-engine codegen: emit **Mirth/NextGen Connect** JS transformers and
   **Rhapsody** mappings — meet engineers in their existing workflow.
2. Real profile validation: wrap the **HAPI validator CLI** with IG packages
   (US Core, national profiles) as a `validate_fhir_hapi` tool.
3. Live terminology: `lookup_terminology` against a tx server (tx.fhir.org /
   LOINC / VSAC) instead of pass-through codes.
4. Standard mapping tables: ship the **HL7 v2-to-FHIR IG** crosswalks as
   reference files behind the mapping skill.

## License

MIT — see [LICENSE](LICENSE).

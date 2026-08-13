---
name: hl7-to-fhir-mapping
description: Map HL7 v2 messages (ORU^R01, ADT, ORM) to FHIR R4 resources with field-level, terminology, and cardinality analysis. Use whenever the user wants to map, convert, or reconcile an HL7 v2 message against FHIR, or asks why a message/resource is being rejected.
---

# HL7 v2 → FHIR R4 mapping

You have deterministic tools. **Never count HL7 field positions by hand and
never invent mappings.** Follow this procedure.

## Procedure

1. **Parse first.** Call `parse_hl7v2` on the raw message. Work only from the
   structured segments it returns — not from eyeballing the pipes.
2. **Build the skeleton.** For ORU^R01, call `hl7_to_fhir_skeleton`. Read its
   `_gaps` array; those are the things you must resolve or flag.
3. **Resolve terminology.** OBX-3 codes are copied through (with a `system`
   only when the message declares one, e.g. `LN` → loinc.org) — not verified.
   Call `lookup_terminology` to verify or translate: code+system does a live
   tx-server `$lookup`; text-only matches the built-in common-lab crosswalk.
   If it can't be confirmed, mark it UNMAPPED — do not guess a LOINC code.
4. **Validate.** Call `validate_fhir` on each resource. For profile-level
   validation (e.g. US Core), call `validate_fhir_hapi` with
   `igs: ["hl7.fhir.us.core#6.1.0"]` — if the jar is missing it returns setup
   instructions; fall back to `validate_fhir` and say the check was base-R4
   structural only. Surface every error and warning verbatim, then explain it.
5. **Generate engine code when asked.** Call `generate_engine_code` with
   `target: "mirth"` or `"rhapsody"` — don't hand-write transformer JS. Review
   the generated code's `notes` and surface them.
6. **Report** using the structure below.

## Reference crosswalks (v2-to-FHIR IG)

Authoritative condensed tables from the HL7 v2-to-FHIR IG live in this skill:

- `references/segment-maps.md` — MSH/PID/PV1/ORC/OBR/OBX/NTE/SPM field-level maps
- `references/datatype-vocab-maps.md` — OBX-2 value[x] crosswalk, v2→FHIR
  datatypes, code-system URIs, status tables (0085/0123/0001)

Read them when mapping any field not covered by the summary tables below, and
cite deviations.

## Segment → resource map (ORU^R01)

| HL7 v2      | FHIR R4            | Notes |
|-------------|--------------------|-------|
| MSH         | MessageHeader / Bundle metadata | sending/receiving app → source |
| PID         | Patient            | PID-3 → identifier, PID-5 → name (family^given), PID-7 → birthDate, PID-8 → gender |
| PV1         | Encounter          | often dropped in lab flows |
| OBR         | DiagnosticReport   | OBR-4 → code, OBR-7 → effectiveDateTime, OBR-22 → issued, OBR-25 → status |
| OBX         | Observation        | OBX-2 → value type, OBX-3 → code (→LOINC), OBX-5 → value[x], OBX-6 → units (→UCUM), OBX-7 → referenceRange, OBX-8 → interpretation, OBX-11 → status |
| NTE         | Observation.note / DiagnosticReport.conclusion | |

## Datatype crosswalk (OBX-2 → FHIR value[x])

- `NM` → `valueQuantity` (unit from OBX-6, ideally UCUM)
- `ST` / `TX` / `FT` → `valueString`
- `CE` / `CWE` → `valueCodeableConcept`
- `SN` (structured numeric) → `valueQuantity` with comparator, or `valueRange`
- `DT` / `TS` → `valueDateTime`

## Status crosswalk (OBX-11 / OBR-25 → status)

`F`→final, `P`→preliminary, `C`→corrected, `X`→cancelled, `I`→registered.
DiagnosticReport and Observation use different value sets — validate both.

## Report format

Always produce:

1. **Mapping table** — HL7 field → FHIR path → value, one row per element.
2. **Terminology** — which codes were translated (local→LOINC/UCUM/SNOMED),
   which are UNMAPPED.
3. **Gaps & risks** — grouped as: *missing required*, *cardinality*,
   *datatype*, *identifier*, *terminology*, *validation errors*.
4. **Transformation code** — when asked. Call `generate_engine_code`:
   - Mirth/NextGen Connect → `target: "mirth"` (JavaScript transformer step)
   - Rhapsody → `target: "rhapsody"` (JavaScript filter/mapper)
   - plain → Python (`hl7apy` + `fhir.resources`) or a FHIR StructureMap
5. **Bundle** — the validated FHIR JSON.

## Guardrails

- This is spec + code work on **test/de-identified** messages. If the message
  looks like real PHI, note it and recommend de-identified samples.
- Cite the HL7 v2-to-FHIR IG mapping direction; flag where you extended beyond
  the standard tables.

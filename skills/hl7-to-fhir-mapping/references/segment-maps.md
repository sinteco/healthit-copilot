# HL7 v2-to-FHIR crosswalk: segment maps

Condensed from the HL7 **v2-to-FHIR IG** (hl7.fhir.uv.v2mappings). Use these
tables as the authoritative baseline; deviations must be flagged in the report.

## MSH → MessageHeader / Bundle

| v2 field | FHIR element | Notes |
|----------|--------------|-------|
| MSH-3    | MessageHeader.source.name | sending application |
| MSH-4    | MessageHeader.source (Organization) | sending facility |
| MSH-5    | MessageHeader.destination.name | receiving application |
| MSH-6    | MessageHeader.destination (Organization) | receiving facility |
| MSH-7    | Bundle.timestamp | message datetime |
| MSH-9    | MessageHeader.eventCoding | ORU^R01 → R01 event |
| MSH-10   | Bundle.identifier | message control ID |
| MSH-11   | — | processing ID (P/T/D); route, don't map |
| MSH-12   | — | version; drives parsing expectations |

## PID → Patient

| v2 field | FHIR element | Notes |
|----------|--------------|-------|
| PID-3    | identifier (0..*) | each repetition → one identifier; CX.4 → system, CX.5 → type |
| PID-5    | name | XPN.1 → family, XPN.2 → given[0], XPN.3 → given[1], XPN.5 → prefix; XPN.7 name-type → HumanName.use |
| PID-7    | birthDate | truncate TS to date |
| PID-8    | gender | table 0001 → administrative-gender (F→female, M→male, O→other, U→unknown, A→other, N→unknown) |
| PID-11   | address | XAD components → line/city/state/postalCode/country |
| PID-13/14| telecom | home / work |
| PID-29/30| deceasedDateTime / deceasedBoolean | |

## PV1 → Encounter (often dropped in lab flows)

| v2 field | FHIR element |
|----------|--------------|
| PV1-2    | class (table 0004 → v3-ActCode: I→IMP, O→AMB, E→EMER) |
| PV1-3    | location.location |
| PV1-7    | participant (attending; PRF/ATND) |
| PV1-19   | identifier (visit number) |
| PV1-44/45| period.start / period.end |

## ORC/OBR → ServiceRequest / DiagnosticReport

| v2 field | FHIR element | Notes |
|----------|--------------|-------|
| ORC-2 / OBR-2 | basedOn (ServiceRequest.identifier) | placer order number |
| ORC-3 / OBR-3 | DiagnosticReport.identifier | filler order number |
| OBR-4    | code | → LOINC where possible |
| OBR-7    | effectiveDateTime (or effectivePeriod.start) | observation datetime |
| OBR-8    | effectivePeriod.end | |
| OBR-16   | — → ServiceRequest.requester | ordering provider |
| OBR-22   | issued | results report datetime |
| OBR-25   | status | table 0123: F→final, C→corrected, P→preliminary, I/S/O→registered, A/R→partial, X→cancelled |

## OBX → Observation

| v2 field | FHIR element | Notes |
|----------|--------------|-------|
| OBX-2    | (drives value[x] datatype) | see datatype crosswalk |
| OBX-3    | code | → LOINC; keep local code as second coding |
| OBX-4    | (sub-id) | grouping for panels/components |
| OBX-5    | value[x] | per OBX-2 |
| OBX-6    | valueQuantity.unit / .code | → UCUM; .system = http://unitsofmeasure.org |
| OBX-7    | referenceRange.text | parse low-high into .low/.high when clean |
| OBX-8    | interpretation | table 0078 → v3-ObservationInterpretation (N, H, L, HH, LL, A, AA, S, R, I) |
| OBX-11   | status | table 0085: F→final, C→corrected, P→preliminary, I/R→registered, S→preliminary, D→entered-in-error, X→cancelled, W→entered-in-error, A→amended |
| OBX-14   | effectiveDateTime | |
| OBX-15   | — | producer ID → performer (Organization) |
| OBX-16   | performer (Practitioner) | |

## NTE → note

- NTE after OBX → Observation.note
- NTE after OBR (before any OBX) → DiagnosticReport.conclusion (or extension)

## SPM → Specimen

| v2 field | FHIR element |
|----------|--------------|
| SPM-2    | identifier |
| SPM-4    | type (→ SNOMED CT specimen hierarchy) |
| SPM-17   | collection.collectedDateTime |

# HL7 v2-to-FHIR crosswalk: datatypes & vocabulary

Condensed from the HL7 **v2-to-FHIR IG** (hl7.fhir.uv.v2mappings).

## OBX-2 value type → Observation.value[x]

| OBX-2 | FHIR value[x] | Notes |
|-------|---------------|-------|
| NM    | valueQuantity | unit from OBX-6 (→ UCUM) |
| SN    | valueQuantity / valueRange / valueRatio | SN.1 comparator (`<`, `>`, `<=`, `>=`) → Quantity.comparator; `^num1^-^num2` → valueRange; `^num1^/^num2` or `^num1^:^num2` → valueRatio |
| ST/TX/FT | valueString | FT may carry `\.br\` formatting — decode |
| CE/CWE | valueCodeableConcept | CWE.1/2/3 → coding.code/display/system; CWE.4-6 alternate coding |
| CF    | valueCodeableConcept | formatted-text display |
| DT    | valueDateTime | date only |
| TM    | valueTime | |
| TS/DTM| valueDateTime | convert HL7 TS format |
| ED/RP | valueAttachment | embedded/referenced data |
| NA    | valueSampledData | numeric array |
| VR    | valueRange | |

## v2 datatype → FHIR datatype

| v2  | FHIR | Key components |
|-----|------|----------------|
| CX  | Identifier | CX.1 → value, CX.4 → system (assigning authority), CX.5 → type |
| XPN | HumanName | XPN.1 family, XPN.2/3 given, XPN.5 prefix, XPN.4 suffix, XPN.7 → use |
| XAD | Address | XAD.1-2 line, XAD.3 city, XAD.4 state, XAD.5 postalCode, XAD.6 country, XAD.7 → use/type |
| XTN | ContactPoint | XTN.2 use-code → use, XTN.3 equipment type → system, XTN.4 (email) / XTN.12 |
| XCN | Practitioner + reference | XCN.1 identifier, XCN.2-6 name |
| CWE/CE | CodeableConcept | triplet 1-3 primary coding, 4-6 alternate, 9 original text |
| EI  | Identifier | EI.1 value, EI.2-4 system |
| TS/DTM | dateTime | YYYYMMDDHHMMSS[.S][+/-ZZZZ] → ISO 8601 |
| PL  | Location | nested point-of-care/room/bed |

## Code system URIs

| v2 coding system id | FHIR system URI |
|---------------------|-----------------|
| LN                  | http://loinc.org |
| SCT / SNM           | http://snomed.info/sct |
| UCUM                | http://unitsofmeasure.org |
| CPT / C4            | http://www.ama-assn.org/go/cpt |
| ICD10 / I10         | http://hl7.org/fhir/sid/icd-10-cm |
| RXNORM / RXN        | http://www.nlm.nih.gov/research/umls/rxnorm |
| CVX                 | http://hl7.org/fhir/sid/cvx |
| NDC                 | http://hl7.org/fhir/sid/ndc |
| HL70078 (interp)    | http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation |
| Local (`L`, `99zzz`)| site-defined — MUST be translated or flagged UNMAPPED |

## Status tables

**Table 0085 (OBX-11) → Observation.status**
F→final, C→corrected, P→preliminary, I→registered, R→registered,
S→preliminary, D→entered-in-error, X→cancelled, W→entered-in-error, A→amended,
U→final (per IG: treat as final-equivalent, flag for review)

**Table 0123 (OBR-25) → DiagnosticReport.status**
F→final, C→corrected, P→preliminary, I→registered, S→registered, A→partial,
R→partial, O→registered, X→cancelled, Y/Z→unknown

**Table 0001 (PID-8) → administrative-gender**
F→female, M→male, O→other, U→unknown, A→other, N→unknown

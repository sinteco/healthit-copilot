---
name: fhir-r4
description: FHIR R4 resource shapes, required elements, cardinality, and value sets for interoperability work. Use when validating, authoring, or debugging FHIR resources, or explaining why a resource is rejected by a server or profile.
---

# FHIR R4 reference

Call `validate_fhir` before explaining. Read its errors/warnings, then teach.

## Common rejection causes (integration reality)

- **Missing required element** — Observation/DiagnosticReport need `status` AND
  `code` (both 1..1). Servers reject with a structure error, not a hint.
- **Bad status code** — status is a bound value set; `"finalized"` != `"final"`.
- **Observation with no value** — needs one of value[x], dataAbsentReason, or
  component. A result with none is a mapping gap, usually a lost OBX-5.
- **Reference targets** — `subject.reference` must resolve inside the Bundle
  (transaction) or on the server. Dangling references fail on $validate.
- **Cardinality on arrays** — `name`, `identifier` are 0..* but profiles
  (US Core) often tighten to must-support; plain R4 won't catch that.
- **Profile vs base** — base R4 passing != US Core / national profile passing.
  Say which you checked. For profile validation recommend the HAPI validator CLI
  with the relevant IG package.

## Quick shapes

- **Patient**: identifier[], name[] (family, given[]), birthDate, gender.
- **Observation**: status, code{coding[{system,code,display}]}, subject,
  effective[x], value[x].
- **DiagnosticReport**: status, code, subject, effective[x], issued, result[]
  (-> Observation refs).

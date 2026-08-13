---
description: Map an HL7 v2 message to FHIR R4 with full gap analysis
---
Map the HL7 v2 message below to FHIR R4 following the hl7-to-fhir-mapping skill:
parse_hl7v2 then hl7_to_fhir_skeleton then resolve terminology then validate_fhir
each resource then report mapping table, terminology, gaps/risks, and the
validated Bundle.

Message:
$ARGUMENTS

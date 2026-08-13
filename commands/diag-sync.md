---
description: Diagnose why patient records or messages are failing to sync
---
Diagnose the sync/interface failure. Parse any HL7 with parse_hl7v2 and validate
any FHIR with validate_fhir. Check the usual suspects in order: line endings,
MSH/encoding, missing required segments/elements, identifier mismatches,
status/value-set errors, dangling references, terminology gaps. Report the most
likely root cause first with evidence from the tool output.

Details:
$ARGUMENTS

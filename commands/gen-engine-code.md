---
description: Generate a Mirth or Rhapsody transformer that maps an HL7 v2 message to FHIR R4
---
Generate integration-engine transformer code for the HL7 v2 message below.
First parse_hl7v2, then call generate_engine_code with the requested target
(mirth or rhapsody — ask if not specified). Present the generated code, explain
its notes, and flag every terminology pass-through that needs a site code map.

Details:
$ARGUMENTS

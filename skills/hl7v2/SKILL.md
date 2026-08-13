---
name: hl7v2
description: HL7 v2.x segment/field reference and common parsing gotchas. Use when reading, debugging, or explaining an HL7 v2 message, or diagnosing why a message is rejected by a receiving system.
---

# HL7 v2.x reference

Always `parse_hl7v2` first — it honors MSH-2 encoding chars so you don't
miscount components.

## Delimiters (from MSH)

MSH-1 = field separator. MSH-2 = encoding chars, normally caret/tilde/backslash/
ampersand: component, repetition, escape, subcomponent. Never assume — read them
from the message.

## Common rejection / interface causes

- **Wrong MSH structure** — MSH-1 is the separator itself; field numbering is
  offset. MSH-9 = message type (ORU^R01), MSH-12 = version.
- **Segment order / required segments** — ORU needs MSH, PID, OBR, OBX. A
  missing PID or OBR breaks most engines.
- **Line endings** — HL7 uses CR. LF or CRLF pasted from editors is the #1
  "why won't it parse" cause. The parser normalizes, but real interfaces may not.
- **Z-segments** — custom segments (Zxx); no standard mapping, flag them.

## Key segments

- **PID**: 3=identifier list, 5=name(family^given^middle), 7=DOB, 8=sex.
- **OBR**: 4=universal service ID (test code), 7=observation datetime, 25=result status.
- **OBX**: 2=value type, 3=observation ID (LOINC ideally), 5=value, 6=units,
  7=reference range, 8=abnormal flags, 11=result status.

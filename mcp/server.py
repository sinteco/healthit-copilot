#!/usr/bin/env python3
"""HealthIT Copilot MCP server.

Deterministic tools so the LLM orchestrates + explains instead of guessing
at field positions, cardinality, or datatypes.

Tools:
  - parse_hl7v2       : parse an HL7 v2 message into structured segments/fields
  - validate_fhir     : structural validation of a FHIR R4 resource
  - hl7_to_fhir_skeleton : build a FHIR Bundle skeleton from an ORU^R01

Design notes:
  - stdlib only, so it runs anywhere python3 exists (no pip step to sell).
  - parsing is spec-correct for delimiters/encoding chars, not a toy split.
  - validation is structural + a curated rule set, NOT a full profile validator.
    For production profile validation, add a `validate_fhir_hapi` tool that
    shells out to the HAPI validator CLI. Kept out of v1 to avoid a Java dep.
"""

import sys
import json
import re


# ----------------------------- MCP plumbing ---------------------------------

def respond(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def text_result(obj):
    """Wrap a python object as an MCP tool text-content result."""
    payload = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
    return {"content": [{"type": "text", "text": payload}]}


# ----------------------------- HL7 v2 parser --------------------------------

def _unescape(text, field_sep, comp_sep, rep_sep, esc, subcomp_sep):
    """Decode HL7 escape sequences (\\F\\ \\S\\ \\T\\ \\R\\ \\E\\, \\Xdd..\\, \\.br\\)."""
    if esc not in text:
        return text
    simple = {"F": field_sep, "S": comp_sep, "T": subcomp_sep,
              "R": rep_sep, "E": esc}
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch != esc:
            out.append(ch)
            i += 1
            continue
        end = text.find(esc, i + 1)
        if end == -1:                        # dangling escape char; keep as-is
            out.append(text[i:])
            break
        body = text[i + 1:end]
        if body in simple:
            out.append(simple[body])
        elif body.startswith("X") and len(body) > 1:
            try:
                out.append(bytes.fromhex(body[1:]).decode("latin-1"))
            except ValueError:
                out.append(esc + body + esc)
        elif body == ".br":
            out.append("\n")
        else:                                # unknown sequence; keep verbatim
            out.append(esc + body + esc)
        i = end + 1
    return "".join(out)


def parse_hl7v2(message: str) -> dict:
    """Parse HL7 v2 into structured segments. Honors MSH encoding chars.

    Returns segments as lists of fields; repetitions and components are split
    and escape sequences decoded, so the LLM never has to count '|' positions
    by hand.
    """
    message = message.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
    lines = [ln for ln in message.split("\r") if ln.strip()]
    if not lines or not lines[0].startswith("MSH"):
        return {"error": "First segment must be MSH. Got: "
                + (lines[0][:20] if lines else "<empty>")}

    msh = lines[0]
    if len(msh) < 4:
        return {"error": "MSH segment too short to contain a field separator."}
    field_sep = msh[3]                       # char after 'MSH'
    enc = msh[4:8] if len(msh) >= 8 else "^~\\&"
    comp_sep, rep_sep, esc, subcomp_sep = (enc + "^~\\&")[:4]

    def decode(text):
        return _unescape(text, field_sep, comp_sep, rep_sep, esc, subcomp_sep)

    def split_field(val):
        reps = val.split(rep_sep)
        out = []
        for rep in reps:
            comps = rep.split(comp_sep)
            if len(comps) == 1:
                out.append(decode(comps[0]))
            else:
                out.append([[decode(sc) for sc in c.split(subcomp_sep)]
                            if subcomp_sep in c else decode(c)
                            for c in comps])
        return out[0] if len(out) == 1 else out

    segments = []
    for line in lines:
        name = line[:3]
        raw = line.split(field_sep)
        if name == "MSH":
            # MSH-1 is the field separator itself; MSH-2 the encoding chars
            fields = [field_sep, enc] + [split_field(f) for f in raw[2:]]
        else:
            fields = [split_field(f) for f in raw[1:]]
        segments.append({"segment": name, "fields": fields})

    seg_counts = {}
    for s in segments:
        seg_counts[s["segment"]] = seg_counts.get(s["segment"], 0) + 1

    msg_type = None
    for s in segments:
        if s["segment"] == "MSH" and len(s["fields"]) > 8:
            msg_type = s["fields"][8]
            break

    return {
        "message_type": msg_type,
        "encoding": {"field": field_sep, "component": comp_sep,
                     "repetition": rep_sep, "escape": esc,
                     "subcomponent": subcomp_sep},
        "segment_counts": seg_counts,
        "segments": segments,
    }


# ----------------------------- FHIR validator -------------------------------

# Minimal curated required-element rules for common R4 resources.
# Not a substitute for the HAPI validator, but catches the errors that
# actually bite in integration work: missing status/code/subject/cardinality.
FHIR_RULES = {
    "Observation": {
        "required": ["status", "code"],
        "recommended": ["subject", "effectiveDateTime", "value[x]"],
        "value_x": True,
        "status_values": ["registered", "preliminary", "final", "amended",
                          "corrected", "cancelled", "entered-in-error", "unknown"],
    },
    "DiagnosticReport": {
        "required": ["status", "code"],
        "recommended": ["subject", "effectiveDateTime", "issued", "result"],
        "status_values": ["registered", "partial", "preliminary", "final",
                          "amended", "corrected", "appended", "cancelled",
                          "entered-in-error", "unknown"],
    },
    "Patient": {
        "required": [],
        "recommended": ["identifier", "name"],
    },
    "Bundle": {
        "required": ["type"],
        "recommended": ["entry"],
        "status_values": None,
    },
}


def validate_fhir(resource) -> dict:
    if isinstance(resource, str):
        try:
            resource = json.loads(resource)
        except json.JSONDecodeError as e:
            return {"valid": False, "errors": [f"Not valid JSON: {e}"]}

    errors, warnings = [], []
    if not isinstance(resource, dict):
        return {"valid": False,
                "errors": ["Resource must be a JSON object"], "warnings": []}
    rt = resource.get("resourceType")
    if not rt:
        return {"valid": False, "errors": ["Missing resourceType"]}

    rules = FHIR_RULES.get(rt)
    if not rules:
        return {"valid": True, "resourceType": rt,
                "warnings": [f"No curated ruleset for {rt}; only JSON checked."],
                "errors": []}

    for req in rules.get("required", []):
        if req == "value[x]":
            continue
        if req not in resource:
            errors.append(f"{rt}.{req} is required (cardinality 1..1) but missing")

    sv = rules.get("status_values")
    if sv and resource.get("status") and resource["status"] not in sv:
        errors.append(f"{rt}.status = '{resource['status']}' is not in the "
                      f"value set {sv}")

    if rules.get("value_x"):
        has_value = any(k.startswith("value") for k in resource)
        has_dae = "dataAbsentReason" in resource
        has_comp = "component" in resource
        if not (has_value or has_dae or has_comp):
            warnings.append("Observation has no value[x], dataAbsentReason, or "
                            "component — a result with no value is usually a "
                            "mapping gap.")

    for rec in rules.get("recommended", []):
        key = rec.replace("[x]", "")
        if rec == "value[x]":
            continue
        if not any(k == key or k.startswith(key) for k in resource):
            warnings.append(f"{rt}.{rec} recommended but missing")

    if rt == "Bundle":
        for i, entry in enumerate(resource.get("entry", [])):
            inner = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(inner, dict):
                errors.append(f"Bundle.entry[{i}].resource missing or not an object")
                continue
            sub = validate_fhir(inner)
            errors += [f"entry[{i}] ({sub.get('resourceType', '?')}): {e}"
                       for e in sub.get("errors", [])]
            warnings += [f"entry[{i}] ({sub.get('resourceType', '?')}): {w}"
                         for w in sub.get("warnings", [])]
            if (resource.get("type") == "transaction"
                    and "request" not in entry):
                errors.append(f"Bundle.entry[{i}].request is required in a "
                              "transaction Bundle")

    return {"valid": len(errors) == 0, "resourceType": rt,
            "errors": errors, "warnings": warnings}


# ------------------------ ORU^R01 -> FHIR skeleton --------------------------

def _get(fields, idx):
    """1-based HL7 field access; returns '' if absent."""
    i = idx - 1
    return fields[i] if 0 <= i < len(fields) else ""


def _flat(v):
    if isinstance(v, list):
        return "^".join(_flat(x) for x in v)
    return str(v)


def _first_comp(v):
    """First component of a possibly component-split field, flattened."""
    if isinstance(v, list):
        return _flat(v[0]) if v else ""
    return str(v)


def _hl7_ts_to_fhir(ts: str) -> str:
    """Convert an HL7 TS (YYYYMMDDHHMMSS[.S][+/-ZZZZ]) to FHIR date/dateTime."""
    ts = _first_comp(ts)
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
                 r"(?:\.\d+)?([+-]\d{4})?$", ts)
    if not m:
        return ""
    y, mo, d, h, mi, s, tz = m.groups()
    date = y + (f"-{mo}" if mo else "") + (f"-{d}" if d else "")
    if not h:
        return date
    time = f"T{h}:{mi or '00'}:{s or '00'}"
    if tz:
        time += tz[:3] + ":" + tz[3:]
    else:
        time += "Z"       # HL7 TS without offset; assume UTC rather than emit an invalid dateTime
    return date + time


# HL7 table 0001 -> FHIR administrative-gender
_GENDER = {"F": "female", "M": "male", "O": "other", "U": "unknown",
           "A": "other", "N": "unknown"}

# OBX-11 result status -> Observation.status
_OBX_STATUS = {"F": "final", "C": "corrected", "P": "preliminary",
               "I": "registered", "R": "registered", "S": "preliminary",
               "D": "entered-in-error", "X": "cancelled", "W": "entered-in-error",
               "U": "final", "A": "amended"}

# OBR-25 result status -> DiagnosticReport.status
_OBR_STATUS = {"F": "final", "C": "corrected", "P": "preliminary",
               "I": "registered", "S": "registered", "A": "partial",
               "R": "partial", "O": "registered", "X": "cancelled",
               "Y": "unknown", "Z": "unknown"}


def _coding_from_cwe(field, note=None):
    """Build a CodeableConcept from a CWE/CE field (code^text^system)."""
    if isinstance(field, list):
        code = _flat(field[0]) if len(field) > 0 else ""
        text = _flat(field[1]) if len(field) > 1 else ""
        system = _flat(field[2]) if len(field) > 2 else ""
        cc = {}
        if code:
            coding = {"code": code}
            if text:
                coding["display"] = text
            if system:
                coding["system"] = ("http://loinc.org" if system.upper() == "LN"
                                    else system)
            cc["coding"] = [coding]
        cc["text"] = text or code
        if note:
            cc["_note"] = note
        return cc
    val = str(field)
    cc = {"text": val}
    if note:
        cc["_note"] = note
    return cc


def hl7_to_fhir_skeleton(message: str) -> dict:
    """Turn an ORU^R01 into a FHIR transaction Bundle skeleton.

    Maps: PID -> Patient, OBR -> DiagnosticReport, each OBX -> Observation,
    NTE -> Observation.note. Local OBX-3/OBR-4 codes are copied through (with
    system only when the message declares one, e.g. LN -> loinc.org) — NOT
    translated. The skill layer is responsible for terminology resolution +
    gap analysis.
    """
    parsed = parse_hl7v2(message)
    if "error" in parsed:
        return parsed
    segs = parsed["segments"]

    patient = {"resourceType": "Patient", "id": "patient-1"}
    observations, obr = [], None
    obs_refs = []
    gaps = ["OBX-3 codes are pass-through, not verified LOINC",
            "No UCUM validation performed on units"]

    for s in segs:
        name, f = s["segment"], s["fields"]
        if name == "PID":
            pid3 = _get(f, 3)
            if pid3:
                reps = pid3 if (isinstance(pid3, list)
                                and isinstance(pid3[0], list)) else [pid3]
                patient["identifier"] = [{"value": _first_comp(r)} for r in reps
                                         if _first_comp(r)]
            pid5 = _get(f, 5)
            if pid5:
                comps = pid5 if isinstance(pid5, list) else [pid5]
                patient["name"] = [{"family": _flat(comps[0]) if comps else "",
                                    "given": [_flat(comps[1])] if len(comps) > 1 else []}]
            bd = _hl7_ts_to_fhir(_get(f, 7))
            if bd:
                patient["birthDate"] = bd[:10]
            sex = _first_comp(_get(f, 8)).upper()
            if sex:
                patient["gender"] = _GENDER.get(sex, "unknown")
                if sex not in _GENDER:
                    gaps.append(f"PID-8 '{sex}' not in HL7 table 0001; "
                                "mapped to 'unknown'")
        elif name == "OBR":
            obr_status = _first_comp(_get(f, 25)).upper()
            obr = {"resourceType": "DiagnosticReport", "id": "report-1",
                   "status": _OBR_STATUS.get(obr_status, "final"),
                   "code": _coding_from_cwe(
                       _get(f, 4) or "UNMAPPED-OBR-4",
                       note="Verify OBR-4 against LOINC / order catalog"),
                   "subject": {"reference": "Patient/patient-1"},
                   "result": []}
            if obr_status and obr_status not in _OBR_STATUS:
                gaps.append(f"OBR-25 '{obr_status}' unrecognized; "
                            "status defaulted to 'final'")
            eff = _hl7_ts_to_fhir(_get(f, 7))
            if eff:
                obr["effectiveDateTime"] = eff
            issued = _hl7_ts_to_fhir(_get(f, 22))
            if issued:
                obr["issued"] = (issued if "T" in issued
                                 else issued + "T00:00:00Z")
        elif name == "OBX":
            n = len(observations) + 1
            obx_status = _first_comp(_get(f, 11)).upper()
            obs = {"resourceType": "Observation", "id": f"obs-{n}",
                   "status": _OBX_STATUS.get(obx_status, "final"),
                   "code": _coding_from_cwe(
                       _get(f, 3) or "UNMAPPED-OBX-3",
                       note="Translate OBX-3 to LOINC via terminology tool"),
                   "subject": {"reference": "Patient/patient-1"}}
            val = _get(f, 5)
            vtype = _flat(_get(f, 2))
            if vtype in ("NM", "SN"):
                raw = _flat(val)
                m = re.match(r"^([<>]=?)?\s*(-?\d+(?:\.\d+)?)$",
                             raw.replace("^", " ").strip())
                if m:
                    q = {"value": float(m.group(2))}
                    unit = _first_comp(_get(f, 6))
                    if unit:
                        q["unit"] = unit
                    if m.group(1):
                        q["comparator"] = m.group(1)
                    obs["valueQuantity"] = q
                else:
                    obs["valueString"] = raw
            elif vtype in ("CE", "CWE"):
                obs["valueCodeableConcept"] = _coding_from_cwe(val)
            elif val != "":
                obs["valueString"] = _flat(val)
            ref_range = _first_comp(_get(f, 7))
            if ref_range:
                obs["referenceRange"] = [{"text": ref_range}]
            interp = _first_comp(_get(f, 8))
            if interp:
                obs["interpretation"] = [{"coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/"
                              "v3-ObservationInterpretation",
                    "code": interp}]}]
            eff = _hl7_ts_to_fhir(_get(f, 14))
            if eff:
                obs["effectiveDateTime"] = eff
            observations.append(obs)
            obs_refs.append({"reference": f"urn:uuid:obs-{n}"})
        elif name == "NTE" and observations:
            note = _flat(_get(f, 3))
            if note:
                observations[-1].setdefault("note", []).append({"text": note})

    if obr is not None:
        obr["result"] = obs_refs
        if "effectiveDateTime" not in obr:
            gaps.append("OBR-7 empty; DiagnosticReport.effective[x] not set")

    def entry(resource):
        rt = resource["resourceType"]
        return {"fullUrl": f"urn:uuid:{resource['id']}",
                "resource": resource,
                "request": {"method": "POST", "url": rt}}

    patient_ref = {"reference": "urn:uuid:patient-1"}
    if obr:
        obr["subject"] = patient_ref
    for o in observations:
        o["subject"] = patient_ref

    entries = [entry(patient)]
    if obr:
        entries.append(entry(obr))
    entries += [entry(o) for o in observations]

    return {"resourceType": "Bundle", "type": "transaction",
            "entry": entries,
            "_gaps": gaps}


# ----------------------------- dispatch -------------------------------------

TOOLS = [
    {"name": "parse_hl7v2",
     "description": "Parse an HL7 v2 message into structured segments and "
                    "fields, honoring MSH encoding characters. Use before "
                    "reasoning about field positions.",
     "inputSchema": {"type": "object",
                     "properties": {"message": {"type": "string"}},
                     "required": ["message"]}},
    {"name": "validate_fhir",
     "description": "Structurally validate a FHIR R4 resource (JSON string or "
                    "object): required elements, status value sets, "
                    "Observation value[x]. Returns errors + warnings.",
     "inputSchema": {"type": "object",
                     "properties": {"resource": {"type": ["string", "object"]}},
                     "required": ["resource"]}},
    {"name": "hl7_to_fhir_skeleton",
     "description": "Convert an ORU^R01 message into a FHIR transaction Bundle "
                    "skeleton (Patient + DiagnosticReport + Observations). Codes "
                    "are pass-through; terminology mapping is left to the skill.",
     "inputSchema": {"type": "object",
                     "properties": {"message": {"type": "string"}},
                     "required": ["message"]}},
]

HANDLERS = {
    "parse_hl7v2": lambda a: parse_hl7v2(a["message"]),
    "validate_fhir": lambda a: validate_fhir(a["resource"]),
    "hl7_to_fhir_skeleton": lambda a: hl7_to_fhir_skeleton(a["message"]),
}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, id_ = req.get("method"), req.get("id")

        if method == "initialize":
            respond(id_, {"protocolVersion": "2024-11-05",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "healthit", "version": "0.1.0"}})
        elif method == "tools/list":
            respond(id_, {"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            handler = HANDLERS.get(name)
            if not handler:
                respond(id_, error={"code": -32601,
                                    "message": f"Unknown tool: {name}"})
            else:
                try:
                    respond(id_, text_result(handler(args)))
                except Exception as e:
                    respond(id_, text_result({"error": str(e)}))
        elif method == "notifications/initialized":
            pass
        else:
            if id_ is not None:
                respond(id_, error={"code": -32601, "message": f"Unknown method: {method}"})


if __name__ == "__main__":
    main()

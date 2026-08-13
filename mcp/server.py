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

def parse_hl7v2(message: str) -> dict:
    """Parse HL7 v2 into structured segments. Honors MSH encoding chars.

    Returns segments as lists of fields; repetitions and components are split
    so the LLM never has to count '|' positions by hand.
    """
    message = message.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
    lines = [ln for ln in message.split("\r") if ln.strip()]
    if not lines or not lines[0].startswith("MSH"):
        return {"error": "First segment must be MSH. Got: "
                + (lines[0][:20] if lines else "<empty>")}

    msh = lines[0]
    field_sep = msh[3]                       # char after 'MSH'
    enc = msh[4:8] if len(msh) >= 8 else "^~\\&"
    comp_sep, rep_sep, esc, subcomp_sep = (enc + "^~\\&")[:4]

    def split_field(val):
        reps = val.split(rep_sep)
        out = []
        for rep in reps:
            comps = rep.split(comp_sep)
            if len(comps) == 1:
                out.append(comps[0])
            else:
                out.append([c.split(subcomp_sep) if subcomp_sep in c else c
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
}


def validate_fhir(resource) -> dict:
    if isinstance(resource, str):
        try:
            resource = json.loads(resource)
        except json.JSONDecodeError as e:
            return {"valid": False, "errors": [f"Not valid JSON: {e}"]}

    errors, warnings = [], []
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


def hl7_to_fhir_skeleton(message: str) -> dict:
    """Turn an ORU^R01 into a FHIR transaction Bundle skeleton.

    Maps: PID -> Patient, OBR -> DiagnosticReport, each OBX -> Observation.
    This is a SKELETON: OBX-3 codes are copied through, NOT translated to LOINC.
    The skill layer is responsible for terminology resolution + gap analysis.
    """
    parsed = parse_hl7v2(message)
    if "error" in parsed:
        return parsed
    segs = parsed["segments"]

    patient = {"resourceType": "Patient", "id": "patient-1"}
    observations, obr = [], None
    obs_refs = []

    for s in segs:
        name, f = s["segment"], s["fields"]
        if name == "PID":
            pid3 = _get(f, 3)
            patient["identifier"] = [{"value": _flat(pid3)}] if pid3 else []
            pid5 = _get(f, 5)
            if pid5:
                comps = pid5 if isinstance(pid5, list) else [pid5]
                patient["name"] = [{"family": _flat(comps[0]) if comps else "",
                                    "given": [_flat(comps[1])] if len(comps) > 1 else []}]
        elif name == "OBR":
            obr = {"resourceType": "DiagnosticReport", "id": "report-1",
                   "status": "final",
                   "code": {"text": _flat(_get(f, 4)) or "UNMAPPED-OBR-4"},
                   "subject": {"reference": "Patient/patient-1"},
                   "result": []}
        elif name == "OBX":
            n = len(observations) + 1
            obs = {"resourceType": "Observation", "id": f"obs-{n}",
                   "status": "final",
                   "code": {"text": _flat(_get(f, 3)) or "UNMAPPED-OBX-3",
                            "_note": "Translate OBX-3 to LOINC via terminology tool"},
                   "subject": {"reference": "Patient/patient-1"}}
            val = _get(f, 5)
            vtype = _flat(_get(f, 2))
            if vtype in ("NM",):
                try:
                    obs["valueQuantity"] = {"value": float(_flat(val)),
                                            "unit": _flat(_get(f, 6))}
                except ValueError:
                    obs["valueString"] = _flat(val)
            else:
                obs["valueString"] = _flat(val)
            observations.append(obs)
            obs_refs.append({"reference": f"Observation/obs-{n}"})

    if obr is not None:
        obr["result"] = obs_refs

    entries = [{"resource": patient}]
    if obr:
        entries.append({"resource": obr})
    entries += [{"resource": o} for o in observations]

    return {"resourceType": "Bundle", "type": "transaction",
            "entry": entries,
            "_gaps": ["OBX-3 codes are pass-through text, not LOINC",
                      "No units UCUM validation performed",
                      "effective[x] / issued not populated from OBR-7/OBR-22"]}


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

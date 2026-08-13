#!/usr/bin/env python3
"""HealthIT Copilot MCP server.

Deterministic tools so the LLM orchestrates + explains instead of guessing
at field positions, cardinality, or datatypes.

Tools:
  - parse_hl7v2          : parse an HL7 v2 message into structured segments/fields
  - validate_fhir        : structural validation of a FHIR R4 resource
  - validate_fhir_hapi   : full profile validation via the HL7 validator CLI
                           (optional: needs java + validator_cli.jar)
  - hl7_to_fhir_skeleton : build a FHIR Bundle skeleton from an ORU^R01
  - generate_engine_code : emit a Mirth or Rhapsody JS transformer that mirrors
                           the skeleton mapping (plain ES5, no E4X)
  - lookup_terminology   : verify codes against a terminology server
                           (tx.fhir.org by default) or a built-in lab crosswalk

Design notes:
  - stdlib only, so it runs anywhere python3 exists (no pip step to sell).
  - parsing is spec-correct for delimiters/encoding chars/escapes, not a toy split.
  - validate_fhir is structural + a curated rule set; validate_fhir_hapi is the
    real profile validator and degrades gracefully when java/jar are absent.
  - the only network access is lookup_terminology, and it sends only
    code + system (never message content). See PRIVACY.md.
"""

import sys
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

__version__ = "0.2.1"


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


# ------------------------ engine codegen (Mirth / Rhapsody) -----------------

def _js_str(s):
    return json.dumps(str(s))


def generate_engine_code(message: str, target: str = "mirth") -> dict:
    """Generate an integration-engine transformer from an ORU^R01 message.

    Targets:
      - mirth    : Mirth / NextGen Connect JavaScript transformer step
                   (E4X msg[...] source, channelMap JSON output)
      - rhapsody : Rhapsody JavaScript filter/mapper (HL7 message input,
                   FHIR JSON output string)

    The generated code mirrors hl7_to_fhir_skeleton's mapping so engine
    behavior matches what was reviewed in Claude Code.
    """
    target = (target or "mirth").lower()
    if target not in ("mirth", "rhapsody"):
        return {"error": f"Unknown target '{target}'. Use 'mirth' or 'rhapsody'."}

    parsed = parse_hl7v2(message)
    if "error" in parsed:
        return parsed

    seg_counts = parsed["segment_counts"]
    has_pid = seg_counts.get("PID", 0) > 0
    has_obr = seg_counts.get("OBR", 0) > 0
    n_obx = seg_counts.get("OBX", 0)

    if target == "mirth":
        code = _mirth_transformer()
        notes = [
            "Paste as a JavaScript transformer step. Plain ES5 with no E4X, so "
            "it runs on classic Rhino-based Mirth Connect AND newer "
            "Nashorn/GraalJS-based versions alike.",
            "Maps from the raw inbound message "
            "(connectorMessage.getRawData()), independent of the channel's "
            "inbound datatype parser.",
            "Output Bundle JSON is stored in channelMap 'fhirBundle'; set the "
            "outbound template / destination to use it "
            "(e.g. HTTP Sender body: ${fhirBundle}).",
            "OBX-3 codes are copied through — wire your site's code map or a "
            "terminology service before go-live.",
        ]
    else:
        code = _rhapsody_mapper()
        notes = [
            "Use in a Rhapsody JavaScript filter with an HL7 v2 input message; "
            "emits one FHIR JSON output message.",
            "Assumes standard delimiters after the engine's inbound parse; "
            "adjust getField/getComp if your route re-encodes.",
            "OBX-3 codes are copied through — add a Rhapsody lookup table for "
            "local-to-LOINC translation.",
        ]

    return {"target": target, "language": "javascript",
            "message_profile": {"segments": seg_counts, "obx_count": n_obx,
                                "has_pid": has_pid, "has_obr": has_obr},
            "code": code, "notes": notes}


CORE_JS = r"""
// mapORU: raw HL7 v2 string -> FHIR R4 transaction Bundle (plain ES5 JS).
// Engine-agnostic: no E4X, no host objects — runs on Rhino, Nashorn,
// GraalJS, and Node. Mirrors the reviewed hl7_to_fhir_skeleton mapping.
function mapORU(raw) {
    raw = String(raw).replace(/\r\n/g, '\r').replace(/\n/g, '\r');
    var lines = raw.split('\r').filter(function (l) { return l.length > 0; });
    if (!lines.length || lines[0].indexOf('MSH') !== 0)
        throw new Error('First segment must be MSH');
    var FS = lines[0].charAt(3);
    var CS = lines[0].charAt(4) || '^';
    var RS = lines[0].charAt(5) || '~';

    // f: 1-based HL7 field on a non-MSH segment line
    function fld(fields, n) { return fields.length > n ? fields[n] : ''; }
    function comp(v, n) {
        var c = String(v === undefined ? '' : v).split(CS);
        return c.length > n ? c[n] : '';
    }

    function hl7ts2fhir(ts) {
        var m = String(ts || '').match(
            /^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.\d+)?([+-]\d{4})?$/);
        if (!m) return '';
        var d = m[1] + (m[2] ? '-' + m[2] : '') + (m[3] ? '-' + m[3] : '');
        if (!m[4]) return d;
        var t = 'T' + m[4] + ':' + (m[5] || '00') + ':' + (m[6] || '00');
        t += m[7] ? m[7].substring(0, 3) + ':' + m[7].substring(3) : 'Z';
        return d + t;
    }

    var GENDER = {F:'female', M:'male', O:'other', U:'unknown', A:'other', N:'unknown'};
    var OBX_STATUS = {F:'final', C:'corrected', P:'preliminary', I:'registered',
                      R:'registered', S:'preliminary', D:'entered-in-error',
                      X:'cancelled', W:'entered-in-error', U:'final', A:'amended'};
    var OBR_STATUS = {F:'final', C:'corrected', P:'preliminary', I:'registered',
                      S:'registered', A:'partial', R:'partial', O:'registered',
                      X:'cancelled', Y:'unknown', Z:'unknown'};

    function cc(field) {
        field = String(field === undefined ? '' : field);
        if (field.indexOf(CS) === -1) return {text: field};
        var code = comp(field, 0), text = comp(field, 1), system = comp(field, 2);
        var out = {};
        if (code) {
            var coding = {code: code};
            if (text) coding.display = text;
            if (system) coding.system = system.toUpperCase() === 'LN'
                ? 'http://loinc.org' : system;
            out.coding = [coding];
        }
        out.text = text || code;
        return out;
    }

    var patient = {resourceType: 'Patient', id: 'patient-1'};
    var report = null;
    var observations = [];
    var obsRefs = [];
    var patientRef = {reference: 'urn:uuid:patient-1'};

    for (var li = 0; li < lines.length; li++) {
        var name = lines[li].substring(0, 3);
        var f = lines[li].split(FS);   // f[1] == field 1 for non-MSH segments
        if (name === 'PID') {
            var pid3 = fld(f, 3);
            if (pid3) {
                patient.identifier = pid3.split(RS)
                    .map(function (r) { return comp(r, 0); })
                    .filter(function (v) { return v; })
                    .map(function (v) { return {value: v}; });
            }
            var pid5 = fld(f, 5);
            if (pid5) {
                var fam = comp(pid5, 0), giv = comp(pid5, 1);
                patient.name = [{family: fam, given: giv ? [giv] : []}];
            }
            var bd = hl7ts2fhir(comp(fld(f, 7), 0));
            if (bd) patient.birthDate = bd.substring(0, 10);
            var sex = comp(fld(f, 8), 0).toUpperCase();
            if (sex) patient.gender = GENDER[sex] || 'unknown';
        } else if (name === 'OBR') {
            var obrStatus = comp(fld(f, 25), 0).toUpperCase();
            report = {resourceType: 'DiagnosticReport', id: 'report-1',
                      status: OBR_STATUS[obrStatus] || 'final',
                      code: cc(fld(f, 4) || 'UNMAPPED-OBR-4'),
                      subject: patientRef, result: []};
            var eff = hl7ts2fhir(comp(fld(f, 7), 0));
            if (eff) report.effectiveDateTime = eff;
            var iss = hl7ts2fhir(comp(fld(f, 22), 0));
            if (iss) report.issued = iss.indexOf('T') > -1 ? iss : iss + 'T00:00:00Z';
        } else if (name === 'OBX') {
            var n = observations.length + 1;
            var obxStatus = comp(fld(f, 11), 0).toUpperCase();
            var obs = {resourceType: 'Observation', id: 'obs-' + n,
                       status: OBX_STATUS[obxStatus] || 'final',
                       code: cc(fld(f, 3) || 'UNMAPPED-OBX-3'),
                       subject: patientRef};
            var vtype = fld(f, 2), val = fld(f, 5);
            if (vtype === 'NM' || vtype === 'SN') {
                var m = String(val).replace(new RegExp('\\' + CS, 'g'), ' ')
                    .trim().match(/^([<>]=?)?\s*(-?\d+(?:\.\d+)?)$/);
                if (m) {
                    var q = {value: parseFloat(m[2])};
                    var unit = comp(fld(f, 6), 0);
                    if (unit) q.unit = unit;
                    if (m[1]) q.comparator = m[1];
                    obs.valueQuantity = q;
                } else {
                    obs.valueString = String(val);
                }
            } else if (vtype === 'CE' || vtype === 'CWE') {
                obs.valueCodeableConcept = cc(val);
            } else if (val !== '') {
                obs.valueString = String(val);
            }
            var rr = comp(fld(f, 7), 0);
            if (rr) obs.referenceRange = [{text: rr}];
            var interp = comp(fld(f, 8), 0);
            if (interp) obs.interpretation = [{coding: [{
                system: 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
                code: interp}]}];
            var oeff = hl7ts2fhir(comp(fld(f, 14), 0));
            if (oeff) obs.effectiveDateTime = oeff;
            observations.push(obs);
            obsRefs.push({reference: 'urn:uuid:obs-' + n});
        } else if (name === 'NTE' && observations.length) {
            var note = fld(f, 3);
            if (note) {
                var last = observations[observations.length - 1];
                if (!last.note) last.note = [];
                last.note.push({text: note});
            }
        }
    }

    if (report) report.result = obsRefs;

    function entry(res) {
        return {fullUrl: 'urn:uuid:' + res.id, resource: res,
                request: {method: 'POST', url: res.resourceType}};
    }
    var entries = [entry(patient)];
    if (report) entries.push(entry(report));
    for (var oi = 0; oi < observations.length; oi++)
        entries.push(entry(observations[oi]));

    return {resourceType: 'Bundle', type: 'transaction', entry: entries};
}
"""

MIRTH_WRAPPER = """\
// HealthIT Copilot — Mirth/NextGen Connect transformer step: ORU^R01 -> FHIR R4
// Plain ES5, no E4X: works on Rhino-based Mirth AND newer Nashorn/GraalJS
// engines. Reads the raw inbound message, not the E4X `msg` tree.
""" + "%CORE%" + """
var bundle = mapORU(connectorMessage.getRawData());
channelMap.put('fhirBundle', JSON.stringify(bundle));
"""

RHAPSODY_WRAPPER = """\
// HealthIT Copilot — Rhapsody JavaScript mapper: ORU^R01 -> FHIR R4 Bundle
// Attach to a JavaScript filter; input HL7 v2, output FHIR JSON.
""" + "%CORE%" + """
var output = JSON.stringify(mapORU(input.text()), null, 2);
"""


def _mirth_transformer():
    return MIRTH_WRAPPER.replace("%CORE%", CORE_JS)


def _rhapsody_mapper():
    return RHAPSODY_WRAPPER.replace("%CORE%", CORE_JS)


# ------------------------ HAPI validator wrapper -----------------------------

def _find_hapi_jar():
    """Locate validator_cli.jar: $HAPI_VALIDATOR_JAR, then common paths."""
    candidates = [os.environ.get("HAPI_VALIDATOR_JAR")]
    home = os.path.expanduser("~")
    candidates += [os.path.join(home, ".healthit", "validator_cli.jar"),
                   os.path.join(home, "validator_cli.jar"),
                   "validator_cli.jar"]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def validate_fhir_hapi(resource, igs=None) -> dict:
    """Full profile validation via the official HL7 validator CLI (HAPI).

    Requires java on PATH and validator_cli.jar (set $HAPI_VALIDATOR_JAR or
    put it at ~/.healthit/validator_cli.jar; download from
    https://github.com/hapifhir/org.hl7.fhir.core/releases).

    igs: list of IG package ids, e.g. ["hl7.fhir.us.core#6.1.0"].
    """
    if isinstance(resource, str):
        try:
            resource = json.loads(resource)
        except json.JSONDecodeError as e:
            return {"valid": False, "errors": [f"Not valid JSON: {e}"]}

    jar = _find_hapi_jar()
    if not jar:
        return {"error": "validator_cli.jar not found. Set $HAPI_VALIDATOR_JAR "
                         "or place it at ~/.healthit/validator_cli.jar. "
                         "Download: https://github.com/hapifhir/org.hl7.fhir.core/"
                         "releases/latest/download/validator_cli.jar",
                "fallback": "Use validate_fhir for structural checks meanwhile."}
    if not shutil.which("java"):
        return {"error": "java not found on PATH (HAPI validator needs a JRE).",
                "fallback": "Use validate_fhir for structural checks meanwhile."}

    with tempfile.TemporaryDirectory() as td:
        res_path = os.path.join(td, "resource.json")
        out_path = os.path.join(td, "outcome.json")
        with open(res_path, "w") as fh:
            json.dump(resource, fh)
        cmd = ["java", "-jar", jar, res_path, "-version", "4.0.1",
               "-output", out_path]
        for ig in (igs or []):
            cmd += ["-ig", ig]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=300)
        except subprocess.TimeoutExpired:
            return {"error": "HAPI validator timed out after 300s."}
        try:
            with open(out_path) as fh:
                outcome = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {"error": "HAPI validator produced no OperationOutcome.",
                    "exit_code": proc.returncode,
                    "stderr_tail": proc.stderr[-2000:]}

    issues = outcome.get("issue", [])
    def _fmt(i):
        loc = (i.get("expression") or i.get("location") or [""])
        return {"severity": i.get("severity"),
                "location": loc[0] if loc else "",
                "details": (i.get("details") or {}).get("text", "")}
    errors = [_fmt(i) for i in issues if i.get("severity") in ("error", "fatal")]
    warnings = [_fmt(i) for i in issues if i.get("severity") == "warning"]
    return {"valid": not errors, "validator": "hapi",
            "igs": igs or [], "errors": errors, "warnings": warnings,
            "information_count": sum(1 for i in issues
                                     if i.get("severity") == "information")}


# ------------------------ terminology lookup ---------------------------------

# Offline mini-crosswalk: the lab analytes that show up in almost every ORU
# feed. Source: LOINC top lab codes (CC BY, LOINC is (c) Regenstrief Institute).
COMMON_LAB_LOINC = {
    "WBC":   {"code": "6690-2",  "display": "Leukocytes [#/volume] in Blood by Automated count"},
    "RBC":   {"code": "789-8",   "display": "Erythrocytes [#/volume] in Blood by Automated count"},
    "HGB":   {"code": "718-7",   "display": "Hemoglobin [Mass/volume] in Blood"},
    "HCT":   {"code": "4544-3",  "display": "Hematocrit [Volume Fraction] of Blood by Automated count"},
    "PLT":   {"code": "777-3",   "display": "Platelets [#/volume] in Blood by Automated count"},
    "GLU":   {"code": "2345-7",  "display": "Glucose [Mass/volume] in Serum or Plasma"},
    "GLUCOSE": {"code": "2345-7", "display": "Glucose [Mass/volume] in Serum or Plasma"},
    "NA":    {"code": "2951-2",  "display": "Sodium [Moles/volume] in Serum or Plasma"},
    "K":     {"code": "2823-3",  "display": "Potassium [Moles/volume] in Serum or Plasma"},
    "CL":    {"code": "2075-0",  "display": "Chloride [Moles/volume] in Serum or Plasma"},
    "CO2":   {"code": "2028-9",  "display": "Carbon dioxide, total [Moles/volume] in Serum or Plasma"},
    "BUN":   {"code": "3094-0",  "display": "Urea nitrogen [Mass/volume] in Serum or Plasma"},
    "CREAT": {"code": "2160-0",  "display": "Creatinine [Mass/volume] in Serum or Plasma"},
    "CREATININE": {"code": "2160-0", "display": "Creatinine [Mass/volume] in Serum or Plasma"},
    "CA":    {"code": "17861-6", "display": "Calcium [Mass/volume] in Serum or Plasma"},
    "ALT":   {"code": "1742-6",  "display": "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma"},
    "AST":   {"code": "1920-8",  "display": "Aspartate aminotransferase [Enzymatic activity/volume] in Serum or Plasma"},
    "TBIL":  {"code": "1975-2",  "display": "Bilirubin.total [Mass/volume] in Serum or Plasma"},
    "ALB":   {"code": "1751-7",  "display": "Albumin [Mass/volume] in Serum or Plasma"},
    "TSH":   {"code": "3016-3",  "display": "Thyrotropin [Units/volume] in Serum or Plasma"},
    "A1C":   {"code": "4548-4",  "display": "Hemoglobin A1c/Hemoglobin.total in Blood"},
    "HBA1C": {"code": "4548-4",  "display": "Hemoglobin A1c/Hemoglobin.total in Blood"},
    "CHOL":  {"code": "2093-3",  "display": "Cholesterol [Mass/volume] in Serum or Plasma"},
    "TRIG":  {"code": "2571-8",  "display": "Triglyceride [Mass/volume] in Serum or Plasma"},
    "HDL":   {"code": "2085-9",  "display": "Cholesterol in HDL [Mass/volume] in Serum or Plasma"},
    "LDL":   {"code": "13457-7", "display": "Cholesterol in LDL [Mass/volume] in Serum or Plasma by calculation"},
    "INR":   {"code": "6301-6",  "display": "INR in Platelet poor plasma by Coagulation assay"},
    "PT":    {"code": "5902-2",  "display": "Prothrombin time (PT)"},
    "PTT":   {"code": "14979-9", "display": "aPTT in Platelet poor plasma by Coagulation assay"},
    "CRP":   {"code": "1988-5",  "display": "C reactive protein [Mass/volume] in Serum or Plasma"},
}

TX_SERVER = os.environ.get("HEALTHIT_TX_SERVER", "https://tx.fhir.org/r4")


def lookup_terminology(code: str = "", system: str = "http://loinc.org",
                       text: str = "", offline: bool = False) -> dict:
    """Look up / verify a terminology code.

    - code + system: verified via the tx server's CodeSystem/$lookup.
    - text only: matched against the built-in common-lab-LOINC crosswalk.
    - offline=True (or network failure): built-in crosswalk only.

    Set $HEALTHIT_TX_SERVER to use a different terminology server
    (default https://tx.fhir.org/r4). Never send PHI to a public tx server.
    """
    result = {"query": {"code": code, "system": system, "text": text},
              "source": None, "match": None, "candidates": []}

    key = (text or code or "").strip().upper()
    builtin = COMMON_LAB_LOINC.get(key)
    if builtin:
        result["candidates"].append({"system": "http://loinc.org",
                                     "confidence": "builtin-crosswalk",
                                     **builtin})

    if offline or not code:
        result["source"] = "builtin"
        result["match"] = result["candidates"][0] if result["candidates"] else None
        if not result["match"]:
            result["note"] = ("No builtin match. Retry with offline=false and a "
                              "code+system for a live $lookup, or mark UNMAPPED.")
        return result

    url = (TX_SERVER.rstrip("/") + "/CodeSystem/$lookup?system="
           + urllib.parse.quote(system, safe="") + "&code="
           + urllib.parse.quote(code, safe=""))
    req = urllib.request.Request(url, headers={
        "Accept": "application/fhir+json",
        "User-Agent": "healthit-copilot/" + __version__})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.load(resp)
        params = {p.get("name"): p.get("valueString", p.get("valueCode", ""))
                  for p in body.get("parameter", []) if "name" in p}
        result["source"] = TX_SERVER
        result["match"] = {"system": system, "code": code,
                           "display": params.get("display", ""),
                           "code_system_name": params.get("name", ""),
                           "confidence": "tx-server-verified"}
        return result
    except urllib.error.HTTPError as e:
        result["source"] = TX_SERVER
        result["tx_error"] = (f"HTTP {e.code}: code '{code}' not resolvable in "
                              f"{system} (or server rejected the request).")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        result["source"] = "builtin (tx server unreachable)"
        result["tx_error"] = f"Terminology server unreachable: {e}"

    result["match"] = result["candidates"][0] if result["candidates"] else None
    if not result["match"]:
        result["note"] = "Unverified: mark the code UNMAPPED rather than guessing."
    return result


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
    {"name": "generate_engine_code",
     "description": "Generate an integration-engine transformer (Mirth/NextGen "
                    "Connect JavaScript or Rhapsody JavaScript) that maps the "
                    "given ORU^R01 to a FHIR R4 transaction Bundle, mirroring "
                    "hl7_to_fhir_skeleton's mapping. target: 'mirth'|'rhapsody'.",
     "inputSchema": {"type": "object",
                     "properties": {"message": {"type": "string"},
                                    "target": {"type": "string",
                                               "enum": ["mirth", "rhapsody"]}},
                     "required": ["message"]}},
    {"name": "validate_fhir_hapi",
     "description": "Full FHIR profile validation via the official HL7 "
                    "validator CLI (requires java + validator_cli.jar; set "
                    "$HAPI_VALIDATOR_JAR). Pass igs like "
                    "['hl7.fhir.us.core#6.1.0'] for profile validation. Falls "
                    "back with instructions if the jar/java is missing.",
     "inputSchema": {"type": "object",
                     "properties": {"resource": {"type": ["string", "object"]},
                                    "igs": {"type": "array",
                                            "items": {"type": "string"}}},
                     "required": ["resource"]}},
    {"name": "lookup_terminology",
     "description": "Verify or find a terminology code. code+system does a "
                    "live CodeSystem/$lookup on the tx server "
                    "($HEALTHIT_TX_SERVER, default tx.fhir.org/r4); text-only "
                    "matches a built-in common-lab LOINC crosswalk. Use for "
                    "OBX-3 local-to-LOINC translation. Never send PHI.",
     "inputSchema": {"type": "object",
                     "properties": {"code": {"type": "string"},
                                    "system": {"type": "string"},
                                    "text": {"type": "string"},
                                    "offline": {"type": "boolean"}},
                     "required": []}},
]

HANDLERS = {
    "parse_hl7v2": lambda a: parse_hl7v2(a["message"]),
    "validate_fhir": lambda a: validate_fhir(a["resource"]),
    "hl7_to_fhir_skeleton": lambda a: hl7_to_fhir_skeleton(a["message"]),
    "generate_engine_code": lambda a: generate_engine_code(
        a["message"], a.get("target", "mirth")),
    "validate_fhir_hapi": lambda a: validate_fhir_hapi(
        a["resource"], a.get("igs")),
    "lookup_terminology": lambda a: lookup_terminology(
        a.get("code", ""), a.get("system", "http://loinc.org"),
        a.get("text", ""), a.get("offline", False)),
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
                          "serverInfo": {"name": "healthit",
                                         "version": __version__}})
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

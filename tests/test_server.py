#!/usr/bin/env python3
"""Tests for the HealthIT Copilot MCP server (stdlib unittest, no deps).

Run:  python3 -m unittest discover tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import server  # noqa: E402


ORU = "\r".join([
    "MSH|^~\\&|LAB|HOSP|EMR|HOSP|20240101120000||ORU^R01|MSG001|P|2.5",
    "PID|1||MRN123^^^HOSP~SSN999^^^SSA||Doe^Jane^Q||19800115|F",
    "OBR|1||ORD1|CBC^Complete Blood Count^L|||20240101083000|||||||||||||||20240101110000|||F",
    "OBX|1|NM|6690-2^WBC^LN||7.2|10*3/uL|4.0-11.0|N|||F|||20240101083000",
    "NTE|1||Slightly elevated after exercise.",
    "OBX|2|ST|COLOR^Specimen Color^L||Amber||||||F",
    "OBX|3|CE|1234^Blood Type^L||A^Type A^L||||||F",
])


class TestParseHL7v2(unittest.TestCase):
    def test_basic_parse(self):
        out = server.parse_hl7v2(ORU)
        self.assertNotIn("error", out)
        self.assertEqual(out["segment_counts"]["OBX"], 3)
        self.assertEqual(out["encoding"]["field"], "|")
        self.assertEqual(server._flat(out["message_type"]), "ORU^R01")

    def test_msh_field_offset(self):
        out = server.parse_hl7v2(ORU)
        msh = out["segments"][0]["fields"]
        self.assertEqual(msh[0], "|")        # MSH-1
        self.assertEqual(msh[1], "^~\\&")    # MSH-2
        self.assertEqual(msh[2], "LAB")      # MSH-3

    def test_newline_normalization(self):
        for sep in ("\n", "\r\n"):
            out = server.parse_hl7v2(ORU.replace("\r", sep))
            self.assertEqual(out["segment_counts"]["OBX"], 3)

    def test_custom_delimiters(self):
        msg = "MSH#*~\\&#LAB#HOSP#EMR#HOSP#202401##ORU*R01#1#P#2.5\rPID#1##MRN1"
        out = server.parse_hl7v2(msg)
        self.assertEqual(out["encoding"]["field"], "#")
        self.assertEqual(out["encoding"]["component"], "*")
        self.assertEqual(server._flat(out["message_type"]), "ORU^R01")

    def test_repetitions_and_components(self):
        out = server.parse_hl7v2(ORU)
        pid = next(s for s in out["segments"] if s["segment"] == "PID")
        pid3 = pid["fields"][2]              # PID-3, two repetitions
        self.assertEqual(len(pid3), 2)
        self.assertEqual(pid3[0][0], "MRN123")

    def test_escape_sequences(self):
        msg = ("MSH|^~\\&|LAB|HOSP|EMR|HOSP|202401||ORU^R01|1|P|2.5\r"
               "OBX|1|ST|X||A \\F\\ B \\S\\ C \\T\\ D \\R\\ E \\E\\ F||||||F")
        out = server.parse_hl7v2(msg)
        obx5 = out["segments"][1]["fields"][4]
        self.assertEqual(obx5, "A | B ^ C & D ~ E \\ F")

    def test_hex_and_br_escapes(self):
        msg = ("MSH|^~\\&|A|B|C|D|202401||ORU^R01|1|P|2.5\r"
               "NTE|1||line1\\.br\\line2 \\X41\\")
        out = server.parse_hl7v2(msg)
        self.assertEqual(out["segments"][1]["fields"][2], "line1\nline2 A")

    def test_rejects_non_msh(self):
        self.assertIn("error", server.parse_hl7v2("PID|1||X"))
        self.assertIn("error", server.parse_hl7v2(""))
        self.assertIn("error", server.parse_hl7v2("MSH"))


class TestValidateFHIR(unittest.TestCase):
    def test_valid_observation(self):
        out = server.validate_fhir({"resourceType": "Observation",
                                    "status": "final", "code": {"text": "x"},
                                    "valueString": "y",
                                    "subject": {"reference": "Patient/1"},
                                    "effectiveDateTime": "2024-01-01"})
        self.assertTrue(out["valid"])
        self.assertEqual(out["errors"], [])

    def test_missing_required(self):
        out = server.validate_fhir({"resourceType": "Observation"})
        self.assertFalse(out["valid"])
        self.assertTrue(any("status" in e for e in out["errors"]))
        self.assertTrue(any("code" in e for e in out["errors"]))

    def test_bad_status_value(self):
        out = server.validate_fhir({"resourceType": "Observation",
                                    "status": "finalized",
                                    "code": {"text": "x"}})
        self.assertFalse(out["valid"])
        self.assertTrue(any("finalized" in e for e in out["errors"]))

    def test_observation_no_value_warns(self):
        out = server.validate_fhir({"resourceType": "Observation",
                                    "status": "final", "code": {"text": "x"}})
        self.assertTrue(out["valid"])
        self.assertTrue(any("value[x]" in w for w in out["warnings"]))

    def test_json_string_input(self):
        out = server.validate_fhir('{"resourceType": "Patient"}')
        self.assertTrue(out["valid"])
        self.assertFalse(server.validate_fhir("{not json")["valid"])

    def test_missing_resource_type(self):
        self.assertFalse(server.validate_fhir({})["valid"])
        self.assertFalse(server.validate_fhir([1, 2])["valid"])

    def test_unknown_type_passes_with_warning(self):
        out = server.validate_fhir({"resourceType": "Medication"})
        self.assertTrue(out["valid"])
        self.assertTrue(out["warnings"])

    def test_bundle_recursive_validation(self):
        bundle = {"resourceType": "Bundle", "type": "transaction",
                  "entry": [{"resource": {"resourceType": "Observation"},
                             "request": {"method": "POST", "url": "Observation"}}]}
        out = server.validate_fhir(bundle)
        self.assertFalse(out["valid"])
        self.assertTrue(any("entry[0]" in e and "status" in e
                            for e in out["errors"]))

    def test_transaction_entry_needs_request(self):
        bundle = {"resourceType": "Bundle", "type": "transaction",
                  "entry": [{"resource": {"resourceType": "Patient"}}]}
        out = server.validate_fhir(bundle)
        self.assertFalse(out["valid"])
        self.assertTrue(any("request" in e for e in out["errors"]))


class TestSkeleton(unittest.TestCase):
    def setUp(self):
        self.bundle = server.hl7_to_fhir_skeleton(ORU)
        by_type = {}
        for e in self.bundle["entry"]:
            by_type.setdefault(e["resource"]["resourceType"], []).append(e)
        self.by_type = by_type

    def test_bundle_shape(self):
        self.assertEqual(self.bundle["resourceType"], "Bundle")
        self.assertEqual(self.bundle["type"], "transaction")
        for e in self.bundle["entry"]:
            self.assertIn("fullUrl", e)
            self.assertEqual(e["request"]["method"], "POST")

    def test_patient_demographics(self):
        p = self.by_type["Patient"][0]["resource"]
        self.assertEqual(p["birthDate"], "1980-01-15")
        self.assertEqual(p["gender"], "female")
        self.assertEqual(p["name"][0]["family"], "Doe")
        self.assertEqual(p["name"][0]["given"], ["Jane"])
        self.assertEqual([i["value"] for i in p["identifier"]],
                         ["MRN123", "SSN999"])

    def test_diagnostic_report(self):
        dr = self.by_type["DiagnosticReport"][0]["resource"]
        self.assertEqual(dr["status"], "final")           # OBR-25 = F
        self.assertEqual(dr["code"]["coding"][0]["code"], "CBC")
        self.assertEqual(dr["effectiveDateTime"], "2024-01-01T08:30:00Z")
        self.assertEqual(dr["issued"], "2024-01-01T11:00:00Z")
        self.assertEqual(len(dr["result"]), 3)
        self.assertEqual(dr["subject"]["reference"], "urn:uuid:patient-1")

    def test_numeric_observation(self):
        obs = self.by_type["Observation"][0]["resource"]
        self.assertEqual(obs["valueQuantity"]["value"], 7.2)
        self.assertEqual(obs["valueQuantity"]["unit"], "10*3/uL")
        self.assertEqual(obs["code"]["coding"][0]["code"], "6690-2")
        self.assertEqual(obs["code"]["coding"][0]["system"], "http://loinc.org")
        self.assertEqual(obs["referenceRange"][0]["text"], "4.0-11.0")
        self.assertEqual(obs["interpretation"][0]["coding"][0]["code"], "N")
        self.assertEqual(obs["effectiveDateTime"], "2024-01-01T08:30:00Z")

    def test_nte_becomes_note(self):
        obs = self.by_type["Observation"][0]["resource"]
        self.assertEqual(obs["note"][0]["text"],
                         "Slightly elevated after exercise.")

    def test_string_and_coded_observations(self):
        obs2 = self.by_type["Observation"][1]["resource"]
        self.assertEqual(obs2["valueString"], "Amber")
        obs3 = self.by_type["Observation"][2]["resource"]
        self.assertEqual(obs3["valueCodeableConcept"]["coding"][0]["code"], "A")

    def test_skeleton_validates_cleanly(self):
        stripped = json.loads(json.dumps(self.bundle))
        stripped.pop("_gaps")
        out = server.validate_fhir(stripped)
        self.assertEqual(out["errors"], [])

    def test_sn_comparator(self):
        msg = ("MSH|^~\\&|A|B|C|D|202401||ORU^R01|1|P|2.5\r"
               "OBX|1|SN|X^Test||>^5|mg/dL|||||F")
        b = server.hl7_to_fhir_skeleton(msg)
        obs = b["entry"][1]["resource"]
        self.assertEqual(obs["valueQuantity"]["value"], 5.0)
        self.assertEqual(obs["valueQuantity"]["comparator"], ">")

    def test_error_passthrough(self):
        self.assertIn("error", server.hl7_to_fhir_skeleton("garbage"))


class TestTimestamps(unittest.TestCase):
    def test_formats(self):
        f = server._hl7_ts_to_fhir
        self.assertEqual(f("1980"), "1980")
        self.assertEqual(f("198001"), "1980-01")
        self.assertEqual(f("19800115"), "1980-01-15")
        self.assertEqual(f("19800115083000"), "1980-01-15T08:30:00Z")
        self.assertEqual(f("19800115083000+0300"), "1980-01-15T08:30:00+03:00")
        self.assertEqual(f("2024010108"), "2024-01-01T08:00:00Z")
        self.assertEqual(f("not-a-date"), "")
        self.assertEqual(f(""), "")


class TestMCPProtocol(unittest.TestCase):
    """End-to-end test of the JSON-RPC stdio loop."""

    def _run(self, requests):
        server_py = os.path.join(os.path.dirname(__file__), "..",
                                 "mcp", "server.py")
        stdin = "\n".join(json.dumps(r) for r in requests) + "\n"
        proc = subprocess.run([sys.executable, server_py], input=stdin,
                              capture_output=True, text=True, timeout=30)
        return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]

    def test_full_session(self):
        out = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "parse_hl7v2", "arguments": {"message": ORU}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "bogus/method"},
        ])
        by_id = {r["id"]: r for r in out}
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "healthit")
        names = [t["name"] for t in by_id[2]["result"]["tools"]]
        self.assertEqual(sorted(names),
                         ["expand_valueset", "explain_hl7_field",
                          "generate_engine_code",
                          "hl7_to_fhir_skeleton",
                          "lookup_terminology", "parse_hl7v2",
                          "validate_fhir", "validate_fhir_hapi"])
        parsed = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(parsed["segment_counts"]["OBX"], 3)
        self.assertIn("error", by_id[4])
        self.assertIn("error", by_id[5])

    def test_tool_exception_returns_text_error(self):
        out = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "parse_hl7v2", "arguments": {}}},
        ])
        body = json.loads(out[0]["result"]["content"][0]["text"])
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()


def _strip_private(obj):
    """Remove _-prefixed keys (LLM hints like _gaps/_note) recursively."""
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items()
                if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(x) for x in obj]
    return obj


class TestEngineCodegen(unittest.TestCase):
    def test_mirth_output(self):
        out = server.generate_engine_code(ORU, "mirth")
        self.assertEqual(out["target"], "mirth")
        self.assertEqual(out["message_profile"]["obx_count"], 3)
        code = out["code"]
        self.assertIn("channelMap.put('fhirBundle'", code)
        self.assertIn("connectorMessage.getRawData()", code)
        self.assertIn("function mapORU(raw)", code)
        self.assertNotIn("for each", code)   # no E4X: Nashorn/GraalJS safe
        self.assertTrue(out["notes"])

    def test_rhapsody_output(self):
        out = server.generate_engine_code(ORU, "rhapsody")
        self.assertEqual(out["target"], "rhapsody")
        code = out["code"]
        self.assertIn("function mapORU(raw)", code)
        self.assertIn("input.text()", code)
        self.assertIn("var output = ", code)
        self.assertNotIn("for each", code)

    def test_bad_target_and_bad_message(self):
        self.assertIn("error", server.generate_engine_code(ORU, "cloverleaf"))
        self.assertIn("error", server.generate_engine_code("garbage", "mirth"))

    def test_default_target(self):
        self.assertEqual(server.generate_engine_code(ORU)["target"], "mirth")


@unittest.skipUnless(shutil.which("node"), "node not available")
class TestEngineCodegenExecution(unittest.TestCase):
    """Execute the generated JS in Node and diff against the Python skeleton.

    This is the test that catches real generation bugs (e.g. engine-specific
    syntax like E4X) instead of just asserting strings appear.
    """

    MESSAGES = {
        "full_oru": ORU,
        "no_pid_no_obr": ("MSH|^~\\&|A|B|C|D|202401||ORU^R01|1|P|2.5\r"
                          "OBX|1|NM|X^T||5|mg|||||F"),
        "sn_comparator": ("MSH|^~\\&|A|B|C|D|202401||ORU^R01|1|P|2.5\r"
                          "PID|1||M1||Doe^Jane||19800101|F\r"
                          "OBR|1||O1|P^Panel|||20240101120000\r"
                          "OBX|1|SN|X^Test||>^5|mg/dL|||||F"),
    }

    def _run_js(self, code, shim, raw_msg, post=""):
        js = ("var RAW = " + json.dumps(raw_msg) + ";\n" + shim + "\n"
              + code + "\n" + post + "\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(js)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True,
                                  text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            return json.loads(proc.stdout)
        finally:
            os.unlink(path)

    def _assert_matches_skeleton(self, target, shim, post=""):
        for label, msg in self.MESSAGES.items():
            with self.subTest(message=label):
                expected = _strip_private(server.hl7_to_fhir_skeleton(msg))
                code = server.generate_engine_code(msg, target)["code"]
                actual = self._run_js(code, shim, msg, post)
                self.assertEqual(actual, expected)

    def test_mirth_js_matches_skeleton(self):
        shim = ("var connectorMessage = {getRawData: function(){return RAW;}};\n"
                "var channelMap = {put: function(k, v){console.log(v);}};")
        self._assert_matches_skeleton("mirth", shim)

    def test_rhapsody_js_matches_skeleton(self):
        shim = "var input = {text: function(){return RAW;}};"
        self._assert_matches_skeleton("rhapsody", shim,
                                      post="console.log(output);")



class TestHapiValidator(unittest.TestCase):
    def test_missing_jar_graceful(self):
        old = os.environ.pop("HAPI_VALIDATOR_JAR", None)
        try:
            with unittest.mock.patch.object(server, "_find_hapi_jar",
                                            return_value=None):
                out = server.validate_fhir_hapi({"resourceType": "Patient"})
            self.assertIn("error", out)
            self.assertIn("validator_cli.jar", out["error"])
            self.assertIn("fallback", out)
        finally:
            if old:
                os.environ["HAPI_VALIDATOR_JAR"] = old

    def test_bad_json_string(self):
        out = server.validate_fhir_hapi("{nope")
        self.assertFalse(out["valid"])

    def test_missing_java_graceful(self):
        with unittest.mock.patch.object(server, "_find_hapi_jar",
                                        return_value="/tmp/fake.jar"), \
             unittest.mock.patch.object(server.shutil, "which",
                                        return_value=None):
            out = server.validate_fhir_hapi({"resourceType": "Patient"})
        self.assertIn("java", out["error"])


class TestTerminology(unittest.TestCase):
    def test_builtin_text_match(self):
        out = server.lookup_terminology(text="WBC", offline=True)
        self.assertEqual(out["match"]["code"], "6690-2")
        self.assertEqual(out["source"], "builtin")

    def test_builtin_case_insensitive(self):
        out = server.lookup_terminology(text="glucose", offline=True)
        self.assertEqual(out["match"]["code"], "2345-7")

    def test_builtin_no_match(self):
        out = server.lookup_terminology(text="ZZZUNKNOWN", offline=True)
        self.assertIsNone(out["match"])
        self.assertIn("note", out)

    def test_network_failure_falls_back(self):
        def boom(*a, **k):
            raise server.urllib.error.URLError("no network")
        with unittest.mock.patch.object(server.urllib.request, "urlopen", boom):
            out = server.lookup_terminology(code="6690-2",
                                            system="http://loinc.org",
                                            text="WBC")
        self.assertIn("tx_error", out)
        self.assertEqual(out["match"]["code"], "6690-2")   # builtin fallback

    def test_tx_lookup_success(self):
        body = json.dumps({"resourceType": "Parameters", "parameter": [
            {"name": "display", "valueString": "Leukocytes"},
            {"name": "name", "valueString": "LOINC"}]}).encode()

        class FakeResp:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with unittest.mock.patch.object(server.urllib.request, "urlopen",
                                        return_value=FakeResp()):
            out = server.lookup_terminology(code="6690-2")
        self.assertEqual(out["match"]["display"], "Leukocytes")
        self.assertEqual(out["match"]["confidence"], "tx-server-verified")


def _seg(name, fields):
    """Build an HL7 segment with 1-based field assignments."""
    parts = [name] + [""] * max(fields)
    for i, v in fields.items():
        parts[i] = v
    return "|".join(parts)


ADT = "\r".join([
    "MSH|^~\\&|REG|HOSP|EMR|HOSP|20240201090000||ADT^A01|MSG002|P|2.5",
    "PID|1||MRN555^^^HOSP||Smith^John||19751120|M",
    _seg("PV1", {2: "I", 3: "ICU^101^A", 19: "V2024001",
                   44: "20240201083000"}),
])

ORM = "\r".join([
    "MSH|^~\\&|CPOE|HOSP|LAB|HOSP|20240301100000||ORM^O01|MSG003|P|2.5",
    "PID|1||MRN777^^^HOSP||Brown^Alice||19900505|F",
    "ORC|NW|PLC123|FIL456||A||||20240301095500",
    "OBR|1|PLC123|FIL456|80048^Basic Metabolic Panel^L|||20240301120000",
])


class TestADTSkeleton(unittest.TestCase):
    def setUp(self):
        self.bundle = server.hl7_to_fhir_skeleton(ADT)
        self.resources = {e["resource"]["resourceType"]: e["resource"]
                          for e in self.bundle["entry"]}

    def test_patient_and_encounter(self):
        self.assertIn("Patient", self.resources)
        self.assertIn("Encounter", self.resources)
        p = self.resources["Patient"]
        self.assertEqual(p["gender"], "male")
        self.assertEqual(p["birthDate"], "1975-11-20")

    def test_encounter_details(self):
        enc = self.resources["Encounter"]
        self.assertEqual(enc["class"]["code"], "IMP")     # PV1-2 = I
        self.assertEqual(enc["status"], "in-progress")    # no PV1-45
        self.assertEqual(enc["identifier"][0]["value"], "V2024001")
        self.assertEqual(enc["period"]["start"], "2024-02-01T08:30:00Z")
        self.assertEqual(enc["subject"]["reference"], "urn:uuid:patient-1")

    def test_finished_when_discharged(self):
        msg = ADT.replace(
            _seg("PV1", {2: "I", 3: "ICU^101^A", 19: "V2024001",
                           44: "20240201083000"}),
            _seg("PV1", {2: "I", 3: "ICU^101^A", 19: "V2024001",
                           44: "20240201083000", 45: "20240205100000"}))
        b = server.hl7_to_fhir_skeleton(msg)
        enc = [e["resource"] for e in b["entry"]
               if e["resource"]["resourceType"] == "Encounter"][0]
        self.assertEqual(enc["status"], "finished")
        self.assertEqual(enc["period"]["end"], "2024-02-05T10:00:00Z")

    def test_validates(self):
        stripped = json.loads(json.dumps(self.bundle))
        stripped.pop("_gaps")
        self.assertEqual(server.validate_fhir(stripped)["errors"], [])

    def test_no_pv1(self):
        msg = "\r".join(ADT.split("\r")[:2])
        b = server.hl7_to_fhir_skeleton(msg)
        self.assertEqual(len(b["entry"]), 1)
        self.assertTrue(any("No PV1" in g for g in b["_gaps"]))


class TestORMSkeleton(unittest.TestCase):
    def setUp(self):
        self.bundle = server.hl7_to_fhir_skeleton(ORM)
        self.resources = {e["resource"]["resourceType"]: e["resource"]
                          for e in self.bundle["entry"]}

    def test_service_request(self):
        sr = self.resources["ServiceRequest"]
        self.assertEqual(sr["status"], "active")         # ORC-5 = A
        self.assertEqual(sr["intent"], "order")
        self.assertEqual(sr["code"]["coding"][0]["code"], "80048")
        self.assertEqual(sr["authoredOn"], "2024-03-01T09:55:00Z")
        self.assertEqual(sr["occurrenceDateTime"], "2024-03-01T12:00:00Z")
        ids = {i["type"]["text"]: i["value"] for i in sr["identifier"]}
        self.assertEqual(ids["Placer Order Number"], "PLC123")
        self.assertEqual(ids["Filler Order Number"], "FIL456")

    def test_validates(self):
        stripped = json.loads(json.dumps(self.bundle))
        stripped.pop("_gaps")
        self.assertEqual(server.validate_fhir(stripped)["errors"], [])

    def test_orc_status_mapping(self):
        msg = ORM.replace("|FIL456||A|", "|FIL456||CM|")
        b = server.hl7_to_fhir_skeleton(msg)
        sr = [e["resource"] for e in b["entry"]
              if e["resource"]["resourceType"] == "ServiceRequest"][0]
        self.assertEqual(sr["status"], "completed")


class TestFMLTarget(unittest.TestCase):
    def test_fml_output(self):
        out = server.generate_engine_code(ORU, "fml")
        self.assertEqual(out["target"], "fml")
        self.assertEqual(out["language"], "fml")
        code = out["code"]
        self.assertIn('map "http://healthit.example/StructureMap/ORU-to-Bundle"', code)
        self.assertIn("group PatientGroup", code)
        self.assertIn("obx5-nm-quantity", code)
        self.assertTrue(any("validator_cli.jar" in n for n in out["notes"]))


class TestExpandValueset(unittest.TestCase):
    def test_requires_url_or_oid(self):
        self.assertIn("error", server.expand_valueset())

    def test_vsac_oid_requires_key(self):
        old = os.environ.pop("UMLS_API_KEY", None)
        try:
            out = server.expand_valueset(oid="2.16.840.1.113883.3.464.1003.104.12.1011")
            self.assertIn("UMLS API key", out["error"])
        finally:
            if old:
                os.environ["UMLS_API_KEY"] = old

    def test_vsac_auth_header(self):
        os.environ["UMLS_API_KEY"] = "test-key-123"
        try:
            req, err = server._tx_request(
                "https://cts.nlm.nih.gov/fhir/ValueSet/$expand?url=x")
            self.assertIsNone(err)
            auth = req.get_header("Authorization")
            self.assertTrue(auth.startswith("Basic "))
            import base64 as b64
            self.assertEqual(b64.b64decode(auth[6:]).decode(),
                             "apikey:test-key-123")
        finally:
            del os.environ["UMLS_API_KEY"]

    def test_expand_success_mocked(self):
        body = json.dumps({"resourceType": "ValueSet", "name": "ObsStatus",
                           "expansion": {"total": 2, "contains": [
                               {"system": "s", "code": "final", "display": "Final"},
                               {"system": "s", "code": "amended", "display": "Amended"},
                           ]}}).encode()

        class FakeResp:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with unittest.mock.patch.object(server.urllib.request, "urlopen",
                                        return_value=FakeResp()):
            out = server.expand_valueset(
                url="http://hl7.org/fhir/ValueSet/observation-status")
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["codes"][0]["code"], "final")

    def test_network_failure(self):
        def boom(*a, **k):
            raise server.urllib.error.URLError("down")
        with unittest.mock.patch.object(server.urllib.request, "urlopen", boom):
            out = server.expand_valueset(url="http://x/vs")
        self.assertIn("unreachable", out["error"])


class TestRegressionHarness(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import regress
        self.regress = regress
        self.tmp = tempfile.mkdtemp()
        self.msgs = os.path.join(self.tmp, "msgs")
        self.base = os.path.join(self.tmp, "base")
        os.makedirs(self.msgs)
        with open(os.path.join(self.msgs, "oru1.hl7"), "w") as fh:
            fh.write(ORU)
        with open(os.path.join(self.msgs, "adt1.hl7"), "w") as fh:
            fh.write(ADT)

    def tearDown(self):
        import shutil as sh
        sh.rmtree(self.tmp)

    def test_baseline_then_match_then_drift(self):
        # first run records baselines
        rc = self.regress.main([self.msgs, "--baseline", self.base])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.base, "oru1.json")))
        # second run matches
        rc = self.regress.main([self.msgs, "--baseline", self.base])
        self.assertEqual(rc, 0)
        # corrupt a baseline -> drift detected
        p = os.path.join(self.base, "oru1.json")
        d = json.load(open(p))
        d["entry"][0]["resource"]["gender"] = "other"
        json.dump(d, open(p, "w"))
        rc = self.regress.main([self.msgs, "--baseline", self.base])
        self.assertEqual(rc, 1)
        # --update repairs it
        rc = self.regress.main([self.msgs, "--baseline", self.base, "--update"])
        self.assertEqual(rc, 0)
        rc = self.regress.main([self.msgs, "--baseline", self.base])
        self.assertEqual(rc, 0)

    def test_bad_dir(self):
        self.assertEqual(
            self.regress.main(["/nonexistent-xyz", "--baseline", self.base]), 2)


class TestFieldDictionary(unittest.TestCase):
    def test_single_field(self):
        out = server.explain_hl7_field("PID", 7)
        self.assertEqual(out["name"], "Date/Time of Birth")
        self.assertEqual(out["datatype"], "TS")       # 2.5 default
        self.assertTrue(out["valid_in_version"])

    def test_datatype_changes_at_27(self):
        self.assertEqual(
            server.explain_hl7_field("PID", 7, "2.8")["datatype"], "DTM")
        self.assertEqual(
            server.explain_hl7_field("OBR", 4, "2.7")["datatype"], "CWE")
        self.assertEqual(
            server.explain_hl7_field("OBR", 4, "2.5.1")["datatype"], "CE")

    def test_withdrawn_field(self):
        out = server.explain_hl7_field("PID", 19, "2.7")
        self.assertFalse(out["valid_in_version"])
        self.assertIn("Withdrawn", out["warning"])
        self.assertTrue(
            server.explain_hl7_field("PID", 19, "2.5")["valid_in_version"])

    def test_field_added_later(self):
        out = server.explain_hl7_field("OBX", 23, "2.4")
        self.assertFalse(out["valid_in_version"])
        self.assertIn("added in v2.5.1", out["warning"])
        self.assertTrue(
            server.explain_hl7_field("OBX", 23, "2.6")["valid_in_version"])

    def test_whole_segment(self):
        out = server.explain_hl7_field("obx")
        self.assertEqual(out["segment"], "OBX")
        nums = [f["field"] for f in out["fields"]]
        self.assertIn("OBX-5", nums)
        self.assertEqual(nums, sorted(nums, key=lambda x: int(x.split("-")[1])))

    def test_errors(self):
        self.assertIn("error", server.explain_hl7_field("ZZZ"))
        self.assertIn("error", server.explain_hl7_field("PID", version="9.9"))
        self.assertIn("error", server.explain_hl7_field("PID", 99))

    def test_msh9_structure_component(self):
        out = server.explain_hl7_field("MSH", 9, "2.3")
        self.assertEqual(out["datatype"], "CM")
        out = server.explain_hl7_field("MSH", 9, "2.4")
        self.assertEqual(out["datatype"], "MSG")

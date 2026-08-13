#!/usr/bin/env python3
"""Tests for the HealthIT Copilot MCP server (stdlib unittest, no deps).

Run:  python3 -m unittest discover tests -v
"""

import json
import os
import subprocess
import sys
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
                         ["generate_engine_code", "hl7_to_fhir_skeleton",
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


class TestEngineCodegen(unittest.TestCase):
    def test_mirth_output(self):
        out = server.generate_engine_code(ORU, "mirth")
        self.assertEqual(out["target"], "mirth")
        self.assertEqual(out["message_profile"]["obx_count"], 3)
        code = out["code"]
        self.assertIn("channelMap.put('fhirBundle'", code)
        self.assertIn("msg['PID']['PID.5']['PID.5.1']", code)
        self.assertIn("OBR_STATUS", code)
        self.assertTrue(out["notes"])

    def test_rhapsody_output(self):
        out = server.generate_engine_code(ORU, "rhapsody")
        self.assertEqual(out["target"], "rhapsody")
        code = out["code"]
        self.assertIn("function transform(input)", code)
        self.assertIn("var output = transform(input);", code)
        self.assertIn("allSegs('OBX')", code)

    def test_no_pid_no_obr(self):
        msg = ("MSH|^~\\&|A|B|C|D|202401||ORU^R01|1|P|2.5\r"
               "OBX|1|NM|X^T||5|mg|||||F")
        out = server.generate_engine_code(msg, "mirth")
        self.assertFalse(out["message_profile"]["has_pid"])
        self.assertNotIn("PID.5", out["code"])
        self.assertNotIn("entry(report)", out["code"])

    def test_bad_target_and_bad_message(self):
        self.assertIn("error", server.generate_engine_code(ORU, "cloverleaf"))
        self.assertIn("error", server.generate_engine_code("garbage", "mirth"))

    def test_default_target(self):
        self.assertEqual(server.generate_engine_code(ORU)["target"], "mirth")


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

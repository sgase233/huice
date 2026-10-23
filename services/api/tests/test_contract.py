"""Static handoff checks, not API acceptance tests. Requires PyYAML and jsonschema."""

import runpy
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
SCHEMAS = CONTRACT["components"]["schemas"]
FORMAT = FormatChecker()


def validator(name):
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]},
        format_checker=FORMAT,
    )


class ContractTests(unittest.TestCase):
    def test_endpoint_inventory_and_unique_ids(self):
        expected = {
            ("get", "/health"), ("get", "/policies"), ("get", "/policies/{policyId}"),
            ("post", "/admin/policies/import"), ("post", "/admin/policies/{policyId}/extract"),
            ("put", "/admin/policies/{policyId}/criteria"), ("post", "/admin/policies/{policyId}/publish"),
            ("post", "/enterprises"), ("get", "/enterprises/{enterpriseId}"),
            ("patch", "/enterprises/{enterpriseId}/facts/{factKey}"),
            ("post", "/assessment-runs"), ("get", "/assessment-runs/{runId}"),
            ("get", "/assessment-runs/{runId}/report.md"),
            ("get", "/assessment-runs/{runId}/trace"),
            ("get", "/admin/policies"), ("get", "/admin/policies/{policyId}"),
        }
        actual = {(method, path) for path, ops in CONTRACT["paths"].items() for method in ops}
        self.assertEqual(actual, expected)
        ids = [op["operationId"] for ops in CONTRACT["paths"].values() for op in ops.values()]
        self.assertEqual(len(set(ids)), 16)
        self.assertEqual(CONTRACT["openapi"], "3.1.0")
        self.assertEqual(CONTRACT["servers"], [{"url": "/api/v1"}])

    def test_all_local_references_resolve(self):
        def visit(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    pointer = value["$ref"]
                    self.assertTrue(pointer.startswith("#/"), pointer)
                    resolved = CONTRACT
                    for part in pointer[2:].split("/"):
                        resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
                    self.assertIsInstance(resolved, dict)
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        visit(CONTRACT)

    def test_schemas_and_examples(self):
        for name, schema in SCHEMAS.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                for example in schema.get("examples", []):
                    validator(name).validate(example)
        for ops in CONTRACT["paths"].values():
            for operation in ops.values():
                for response in operation["responses"].values():
                    for media in response.get("content", {}).values():
                        if "example" in media:
                            schema = {**media["schema"], "components": CONTRACT["components"]}
                            Draft202012Validator(schema, format_checker=FORMAT).validate(media["example"])

    def test_types_are_generated_from_current_contract(self):
        generator = runpy.run_path(str(ROOT / "contracts/generate_types.py"))
        expected = generator["render_types"](CONTRACT)
        actual = (ROOT / "apps/web/src/api/types.ts").read_text(encoding="utf-8")
        self.assertEqual(actual, expected, "Regenerate frontend types before handoff.")
        type_of = generator["ts_type"]
        self.assertEqual(type_of({"type": ["string", "null"]}), "string | null")
        self.assertEqual(type_of({"type": "object", "additionalProperties": {"type": "string"}}), "{\n  [key: string]: string;\n}")
        # anyOf refines non-null location fields; it must not erase the common object shape.
        span_type = type_of(SCHEMAS["SourceSpan"])
        self.assertIn("sourceId: UUID;", span_type)
        self.assertIn("quote: string;", span_type)
        self.assertNotIn("unknown", span_type)

    def test_fact_unknown_zero_and_trust_boundary(self):
        fact = {"key": "taxableIncome", "value": None, "unit": "CNY",
                "period": {"startDate": "2026-01-01", "endDate": "2026-12-31"}, "source": "user_input"}
        for value in (None, "0", "2800000.00"):
            validator("FactInput").validate({**fact, "value": value})
        for change in ({"value": 0}, {"value": ""}, {"value": "null"}, {"unit": "person"},
                       {"verified": True}, {"key": "invented"}, {"source": "model_guess"},
                       {"value": "1.234"}):
            with self.subTest(change=change):
                self.assertFalse(validator("FactInput").is_valid({**fact, **change}))
        boolean_fact = {**fact, "key": "rdActivity", "value": False, "unit": "boolean"}
        validator("FactInput").validate(boolean_fact)
        self.assertFalse(validator("FactInput").is_valid({**boolean_fact, "value": "false"}))
        assets = {**fact, "key": "assetsStart", "value": "0"}
        validator("FactInput").validate(assets)
        self.assertFalse(validator("FactInput").is_valid({**assets, "value": "-1.00"}))

    def test_invalid_ast_source_and_publish_confirmation(self):
        expression = SCHEMAS["CriterionDraft"]["examples"][0]["expression"]
        validator("Expression").validate({"op": "not", "child": expression})
        validator("Expression").validate({"op": "or", "children": [expression, expression]})
        for bad in ({"op": "eval", "code": "True"}, {"op": "and", "children": []},
                    {**expression, "comparator": "approximately"}):
            self.assertFalse(validator("Expression").is_valid(bad))
        span = SCHEMAS["CriterionDraft"]["examples"][0]["sourceSpans"][0]
        self.assertFalse(validator("SourceSpan").is_valid({**span, "page": None, "section": None}))
        published = {**SCHEMAS["CriterionDraft"]["examples"][0], "validationStatus": "passed"}
        validator("PublishedCriterion").validate(published)
        self.assertFalse(validator("PublishedCriterion").is_valid({**published, "validationStatus": "failed"}))
        self.assertFalse(validator("CriterionDraft").is_valid({**SCHEMAS["CriterionDraft"]["examples"][0], "reviewFinding": "conflict"}))
        self.assertFalse(validator("OfficialUrl").is_valid("http://www.gov.cn/"))
        validator("PublishPolicyInput").validate({"version": "v1", "reviewConfirmed": True})
        self.assertFalse(validator("PublishPolicyInput").is_valid({"version": "v1", "reviewConfirmed": False}))

    def test_assessment_request_limits_and_timezones(self):
        request = SCHEMAS["AssessmentInput"]["examples"][0]
        for change in ({"policyIds": []}, {"policyIds": request["policyIds"] * 2},
                       {"policyIds": [f"00000000-0000-4000-8000-{i:012d}" for i in range(8)]},
                       {"asOf": "2026-10-23T10:00:00"}):
            self.assertFalse(validator("AssessmentInput").is_valid({**request, **change}))

    def test_admin_security_concurrency_headers_and_report_media(self):
        for path, ops in CONTRACT["paths"].items():
            for method, operation in ops.items():
                if path.startswith("/admin/") or path.endswith("/trace"):
                    self.assertEqual(operation["security"], [{"DemoSession": [], "AdminBearer": []}])
                if path.endswith(("/extract", "/criteria", "/publish")) or method == "patch":
                    names = {p.get("$ref", "").rsplit("/", 1)[-1] for p in operation["parameters"]}
                    self.assertIn("IfMatch", names)
                    self.assertIn("428", operation["responses"])
                    self.assertIn("409", operation["responses"])
                    self.assertIn("ETag", operation["responses"]["200"]["headers"])
                if "requestBody" in operation and "application/json" in operation["requestBody"]["content"]:
                    self.assertTrue(operation["requestBody"]["required"])
        report = CONTRACT["paths"]["/assessment-runs/{runId}/report.md"]["get"]["responses"]
        self.assertIn("text/markdown", report["200"]["content"])
        self.assertIn("application/json", report["404"]["content"])


if __name__ == "__main__":
    unittest.main()

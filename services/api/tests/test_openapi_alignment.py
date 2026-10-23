"""服务实际暴露的接口必须与 contracts/openapi.yaml 的已实现部分一致。"""

from __future__ import annotations

import pytest
import yaml

from app.main import API_PREFIX, create_app
from support import CONTRACT_PATH

IMPLEMENTED = {
    ("get", "/health"): "getHealth",
    ("post", "/admin/policies/import"): "importPolicy",
    ("get", "/admin/policies"): "listAdminPolicies",
    ("get", "/admin/policies/{policyId}"): "getAdminPolicy",
}


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def schema(app) -> dict:
    return app.openapi()


def test_every_implemented_endpoint_exists_in_contract(contract, schema):
    for method, path in IMPLEMENTED:
        assert path in contract["paths"], f"契约缺少 {path}"
        assert method in contract["paths"][path], f"契约缺少 {method.upper()} {path}"
        served = schema["paths"].get(f"{API_PREFIX}{path}")
        assert served is not None, f"服务未暴露 {path}"
        assert method in served, f"服务未暴露 {method.upper()} {path}"


def test_operation_ids_match(contract, schema):
    for (method, path), operation_id in IMPLEMENTED.items():
        expected = contract["paths"][path][method]["operationId"]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]["operationId"]
        assert actual == expected == operation_id


def test_summaries_and_tags_match(contract, schema):
    for method, path in IMPLEMENTED:
        expected = contract["paths"][path][method]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]
        assert actual["summary"] == expected["summary"]
        assert actual["tags"] == expected["tags"]


def test_security_matches(contract, schema):
    for method, path in IMPLEMENTED:
        expected = contract["paths"][path][method]["security"]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]["security"]
        assert actual == expected


def test_security_schemes_are_served(schema):
    schemes = schema["components"]["securitySchemes"]
    assert schemes["AdminBearer"]["scheme"] == "bearer"
    assert schemes["DemoSession"]["name"] == "huice_demo_session"


def test_unimplemented_endpoints_are_not_served(schema):
    served = {
        (method, path.replace(API_PREFIX, "", 1))
        for path, operations in schema["paths"].items()
        for method in operations
    }
    assert served == set(IMPLEMENTED)


def test_contract_version_is_release_candidate(contract):
    assert contract["info"]["version"] == "1.0.0-rc.2"

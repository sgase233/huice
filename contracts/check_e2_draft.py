"""E2 草案校验器：可复跑、无副作用，只读 contracts/ 下的两份契约。

用法（仓库根目录）::

    python contracts/check_e2_draft.py          # 人或 CI
    python contracts/check_e2_draft.py --quiet  # 只输出计数与结论

依赖 ``PyYAML`` 与 ``jsonschema``（测试环境已有，见 services/api/tests/test_contract.py）。

校验内容：
1. 草案结构：openapi 版本、8 个 operationId 与预期一致且唯一、路径/方法齐全；
2. ``$ref`` 可解析（含跨文件指向 openapi.yaml），外链只允许这两种目标；
3. 权限：每个操作都有 ``x-e1-gate``（local/restricted 两种模式），读操作给审核员会话或
   只读 Bearer、写操作只给会话，没有任何操作把 DemoSession 写进 security（门禁是外层的事）；
4. 条件写：四条写操作都声明 428/400 与 409，提交额外声明 422；错误码表唯一、状态码合法，
   且草案里出现的 E2 专有码都在表里登记（防止「正文提了、表里没有」）；
5. 运行时模型：终态码封闭为 8 个、候选状态 5 个、可编辑态 2 个，且状态机转移自洽
   （终态没有出边；extraction_failed 可经补齐条件转回 pending_review）；
6. 所有请求/响应样例按 schema 校验通过（jsonschema，跨文件 ``$ref``）。

本脚本**不**判断实现是否符合契约——那是
``services/api/tests/test_e2_draft.py`` 的职责（它对照运行中的 FastAPI 路由）。
"""

from __future__ import annotations

import argparse
import re
import sys
import warnings
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

with warnings.catch_warnings():
    # RefResolver 在 jsonschema 4.x 已弃用，但仍是唯一稳定的跨文件引用方案
    warnings.simplefilter("ignore", DeprecationWarning)
    from jsonschema.validators import RefResolver

CONTRACTS = Path(__file__).resolve().parent
DRAFT = CONTRACTS / "openapi.e2-draft.yaml"
BASE = CONTRACTS / "openapi.yaml"

METHODS = {"get", "put", "post", "patch", "delete", "head", "options"}

#: 草案必须存在的 8 个操作（与 services/api/tests/test_openapi_alignment.py 的 E2_OPERATIONS 同源）。
EXPECTED = {
    "getDiscoverySettings": "GET /admin/discovery/settings",
    "updateDiscoverySettings": "PUT /admin/discovery/settings",
    "listDiscoveryRuns": "GET /admin/discovery/runs",
    "listPolicyCandidates": "GET /admin/policy-candidates",
    "getPolicyCandidate": "GET /admin/policy-candidates/{candidateId}",
    "updatePolicyCandidate": "PATCH /admin/policy-candidates/{candidateId}",
    "submitPolicyCandidate": "POST /admin/policy-candidates/{candidateId}/submit",
    "discardPolicyCandidate": "POST /admin/policy-candidates/{candidateId}/discard",
}
WRITE_OPERATIONS = (
    "updateDiscoverySettings",
    "updatePolicyCandidate",
    "submitPolicyCandidate",
    "discardPolicyCandidate",
)
TERMINAL_REASONS = {
    "completed",
    "no_new_policy",
    "fetch_failed",
    "partial_failed",
    "budget_exceeded",
    "oversize",
    "timeout",
    "lock_lost",
}
CANDIDATE_STATES = {
    "pending_review",
    "extraction_failed",
    "submitted",
    "discarded",
    "expired",
}
EDITABLE_STATES = {"pending_review", "extraction_failed"}
#: 草案正文里只属于 E2 的错误码；它们必须出现在 x-e2-errors 表里。
E2_ONLY_CODES = {
    "CANDIDATE_NOT_FOUND",
    "CANDIDATE_STATE_CONFLICT",
    "IDEMPOTENCY_KEY_REUSED",
    "MISSING_IDEMPOTENCY_KEY",
    "INVALID_IDEMPOTENCY_KEY",
}
CODE_RE = re.compile(r"\b[A-Z][A-Z_]{4,}\b")


def _resolve_pointer(doc: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return doc
    node = doc
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        else:
            if part not in node:
                raise KeyError(part)
            node = node[part]
    return node


def _walk_refs(
    node: Any, where: str, draft: dict, base: dict, errors: list[str], counts: dict
) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            counts["refs"] = counts.get("refs", 0) + 1
            if ref.startswith("./openapi.yaml#"):
                target_doc, pointer = base, ref.split("#", 1)[1]
            elif ref.startswith("#"):
                target_doc, pointer = draft, ref.split("#", 1)[1]
            else:
                errors.append(f"{where}: 外链只允许指向 ./openapi.yaml# 或本文件，实际 {ref}")
                return
            try:
                _resolve_pointer(target_doc, pointer)
            except (KeyError, IndexError) as exc:
                errors.append(f"{where}: 无法解析 $ref {ref}（{exc}）")
        for key, value in node.items():
            _walk_refs(value, where if key == "$ref" else f"{where}/{key}", draft, base, errors, counts)
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            _walk_refs(value, f"{where}[{idx}]", draft, base, errors, counts)


def _operations(draft: dict) -> dict[str, dict]:
    """operationId -> {method, path, op}。"""

    found: dict[str, dict] = {}
    for path, item in (draft.get("paths") or {}).items():
        for method, op in item.items():
            if method not in METHODS or not isinstance(op, dict):
                continue
            oid = op.get("operationId")
            if oid in found:
                found[oid]["duplicate"] = True
            found[oid] = {"method": method, "path": path, "op": op}
    return found


def check(draft_path: Path = DRAFT, base_path: Path = BASE) -> tuple[dict, list[str]]:
    """返回 (计数, 错误列表)。错误为空即通过。"""

    draft = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    errors: list[str] = []

    def bump(key: str, amount: int = 1) -> None:
        counts[key] = counts.get(key, 0) + amount

    # 1. 版本与操作集合
    if draft.get("openapi") != "3.1.0":
        errors.append(f"openapi 版本应为 3.1.0，实际 {draft.get('openapi')!r}")
    version = (draft.get("info") or {}).get("version", "")
    if "-draft" not in version:
        errors.append(f"info.version 必须带 -draft 标记（草案不得冒充发布版本），实际 {version!r}")

    ops = _operations(draft)
    for oid, key in ops.items():
        bump("operations")
        if oid not in EXPECTED:
            errors.append(f"草案多出未预期的操作 {oid}")
        if key.get("duplicate"):
            errors.append(f"operationId 重复：{oid}")
        expected_sig = EXPECTED.get(oid)
        if expected_sig:
            actual_sig = f"{key['method'].upper()} {key['path']}"
            if actual_sig != expected_sig:
                errors.append(f"{oid} 的路径应为 {expected_sig}，实际 {actual_sig}")
    for oid in EXPECTED:
        if oid not in ops:
            errors.append(f"草案缺少操作 {oid}")

    # 2. 引用可解析
    _walk_refs(draft, "", draft, base, errors, counts)

    # 3. 权限与门禁
    security_schemes = set((draft.get("components") or {}).get("securitySchemes") or {})
    for oid, key in ops.items():
        op = key["op"]
        gate = op.get("x-e1-gate") or {}
        if set(gate) < {"local", "restricted", "note"}:
            errors.append(f"{oid} 的 x-e1-gate 缺字段：{sorted(gate)}")
        elif gate["local"] != "none" or gate["restricted"] != "DemoSession":
            errors.append(f"{oid} 的门禁取值与 1.1.0-rc.1 口径不符：{gate}")
        security = op.get("security")
        if not isinstance(security, list) or not security:
            errors.append(f"{oid} 缺少 security")
            continue
        schemes = {next(iter(entry)) for entry in security if entry}
        unknown = schemes - security_schemes
        if unknown:
            errors.append(f"{oid} 引用了未定义的 scheme：{unknown}")
        if "DemoSession" in schemes:
            errors.append(f"{oid} 把 DemoSession 写进了 security——门禁只能写在 x-e1-gate")
        if oid in WRITE_OPERATIONS:
            if schemes != {"UserSession"}:
                errors.append(f"{oid} 是写操作，security 只能是 UserSession，实际 {schemes}")
        else:
            if schemes != {"UserSession", "AdminBearer"}:
                errors.append(
                    f"{oid} 是读操作，security 应为 UserSession 或 AdminBearer，实际 {schemes}"
                )

    # 4. 条件写与错误码
    for oid in WRITE_OPERATIONS:
        key = ops.get(oid)
        if not key:
            continue
        responses = key["op"].get("responses") or {}
        for status in ("400", "409", "428"):
            if status not in responses:
                errors.append(f"{oid} 是条件写操作，必须声明 {status}")
        if oid == "submitPolicyCandidate" and "422" not in responses:
            errors.append("submitPolicyCandidate 必须声明 422（缺字段/无条件的拒绝路径）")
        if oid != "updateDiscoverySettings" and "422" not in responses:
            errors.append(f"{oid} 必须声明 422（字段校验）")

    table = ((draft.get("x-e2-errors") or {}).get("codes")) or []
    seen_codes: set[str] = set()
    for row in table:
        bump("error_codes")
        code = row.get("code")
        if not code or not isinstance(code, str):
            errors.append(f"x-e2-errors 有缺 code 的行：{row}")
            continue
        if code in seen_codes:
            errors.append(f"x-e2-errors 里 {code} 重复")
        seen_codes.add(code)
        if not isinstance(row.get("status"), int) or not 400 <= row["status"] <= 599:
            errors.append(f"{code} 的 status 不是合法 HTTP 状态码：{row.get('status')!r}")
        if not row.get("when"):
            errors.append(f"{code} 缺少 when 说明")

    # 正文里提到的 E2 专有码必须都已登记
    body_codes = set(CODE_RE.findall(yaml.safe_dump(draft, allow_unicode=True)))
    for code in E2_ONLY_CODES:
        if code not in seen_codes:
            errors.append(f"x-e2-errors 缺少 {code}")
    for code in body_codes & E2_ONLY_CODES:
        if code not in seen_codes:
            errors.append(f"正文提到 {code}，但 x-e2-errors 未登记")

    # 5. 运行时模型
    model = draft.get("x-e2-runtime-model") or {}
    for key in ("scheduler", "interval", "run-deadline", "lock", "catch-up", "token-budget", "dedup"):
        if not model.get(key):
            errors.append(f"x-e2-runtime-model 缺少 {key}")
    reasons = set((model.get("terminal-reasons") or {}).keys())
    if reasons != TERMINAL_REASONS:
        errors.append(f"终态码应为 {sorted(TERMINAL_REASONS)}，实际 {sorted(reasons)}")
    counts["terminal_reasons"] = len(reasons)

    lifecycle = model.get("candidate-lifecycle") or {}
    states = set(lifecycle.get("states") or [])
    if states != CANDIDATE_STATES:
        errors.append(f"候选状态应为 {sorted(CANDIDATE_STATES)}，实际 {sorted(states)}")
    if set(lifecycle.get("editable") or []) != EDITABLE_STATES:
        errors.append("candidate-lifecycle.editable 应恰为 pending_review / extraction_failed")
    transitions = lifecycle.get("transitions") or {}
    if set(transitions) != CANDIDATE_STATES:
        errors.append("candidate-lifecycle.transitions 未覆盖全部状态")
    for state, targets in transitions.items():
        bump("lifecycle_edges", len(targets or []))
        for target in targets or []:
            if target not in CANDIDATE_STATES:
                errors.append(f"{state} 转移到了未定义状态 {target}")
        if state in CANDIDATE_STATES - EDITABLE_STATES and targets:
            errors.append(f"终态 {state} 不应该有出边：{targets}")
        if state == "extraction_failed" and "pending_review" not in (targets or []):
            errors.append("extraction_failed 必须能经补齐条件转回 pending_review")
    counts["candidate_states"] = len(states)

    guard = model.get("fetch-guards") or {}
    if guard.get("max-body-bytes") != 2 * 1024 * 1024:
        errors.append("fetch-guards.max-body-bytes 应为 2 MiB（与 MAX_BODY_BYTES 同源）")
    if guard.get("per-request-timeout-seconds") != 15:
        errors.append("fetch-guards.per-request-timeout-seconds 应为 15")
    if guard.get("max-redirects") != 5:
        errors.append("fetch-guards.max-redirects 应为 5")
    counts["fetch_guards"] = len(guard)

    # 6. 样例按 schema 校验（RefResolver 可跨文件）
    store: dict[str, dict] = {}
    for key in (
        draft_path.as_uri(),
        base_path.as_uri(),
        draft_path.as_posix(),
        base_path.as_posix(),
        "openapi.yaml",
        "./openapi.yaml",
    ):
        store[key] = draft if "e2-draft" in key else base

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        resolver = RefResolver(base_uri=draft_path.as_uri(), referrer=draft, store=store)
        for oid, key in ops.items():
            op = key["op"]
            for status, response in (op.get("responses") or {}).items():
                if not isinstance(response, dict):
                    continue
                for media in (response.get("content") or {}).values():
                    schema = media.get("schema")
                    if not schema:
                        continue
                    validator = Draft202012Validator(schema, resolver=resolver)
                    for label, value in _examples(media):
                        bump("response_examples")
                        for err in validator.iter_errors(value):
                            errors.append(
                                f"{oid} {status} {label}: {err.message} @ {list(err.path)}"
                            )
            for media in ((op.get("requestBody") or {}).get("content") or {}).values():
                schema = media.get("schema")
                if not schema:
                    continue
                validator = Draft202012Validator(schema, resolver=resolver)
                for label, value in _examples(media):
                    bump("request_examples")
                    for err in validator.iter_errors(value):
                        errors.append(f"{oid} {label}: {err.message} @ {list(err.path)}")

    return counts, errors


def _examples(media: dict) -> list[tuple[str, Any]]:
    """``example:`` 与 ``examples: {name: {value: ...}}`` 两种写法都取。"""

    found: list[tuple[str, Any]] = []
    if "example" in media:
        found.append(("example", media["example"]))
    for name, example in (media.get("examples") or {}).items():
        if isinstance(example, dict) and "value" in example:
            found.append((f"examples[{name}]", example["value"]))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 contracts/openapi.e2-draft.yaml")
    parser.add_argument("--draft", type=Path, default=DRAFT)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--quiet", action="store_true", help="只输出计数与结论")
    args = parser.parse_args(argv)

    counts, errors = check(args.draft, args.base)
    if not args.quiet:
        print(f"draft = {args.draft}")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")
    if errors:
        print(f"\nFAIL：{len(errors)} 个问题")
        for item in errors:
            print("  -", item)
        return 1
    print("\nOK：结构、引用、权限、错误码、运行时模型与样例校验全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

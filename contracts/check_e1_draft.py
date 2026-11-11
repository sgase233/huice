"""E1 草案校验器：可复跑、无副作用，只读 contracts/ 下的两份契约。

用法（仓库根目录）::

    python contracts/check_e1_draft.py          # 人或 CI
    python contracts/check_e1_draft.py --quiet  # 只输出计数与结论

依赖 ``PyYAML`` 与 ``jsonschema``（测试环境已有，见 services/api/tests/test_contract.py）。

校验内容：
1. 草案结构：openapi 版本、operationId 唯一、``$ref`` 可解析（含跨文件指向 openapi.yaml）；
2. 权限模型：7 个新增操作都有 ``x-e1-gate``；``security`` 只引用已定义的 scheme；
   登出豁免门禁、无 401；注册/登录带 Origin 参数；
3. 16 行 ``x-e1-impact`` 与基线 ``openapi.yaml`` 的端点集合一一对应，键齐全；
4. 错误码唯一，且 ``x-e1-impact.newFailures`` 里引用的码都已定义；
   带身份要求的既有端点必须列出 401 族（防止再次漏写 401）；
5. ``RunStatus`` 仍是封闭四值，E3b 的 interrupted 只在 ``x-e1-lifecycle`` 里预留；
6. 门禁模型真值表覆盖 local/restricted 两种模式；
7. 所有请求/响应样例按 schema 校验通过（jsonschema，跨文件 ``$ref``）。

本脚本**不**判断实现是否符合契约——那是
``services/api/tests/test_e1_draft.py`` 的职责（它对照运行中的 FastAPI 路由）。
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
DRAFT = CONTRACTS / "openapi.e1-draft.yaml"
BASE = CONTRACTS / "openapi.yaml"

METHODS = {"get", "put", "post", "patch", "delete", "head", "options"}
IMPACT_KEYS = ("endpoint", "current", "gate", "identity", "roles", "ownership", "csrf", "newFailures")
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


def _walk_refs(node: Any, where: str, draft: dict, base: dict, errors: list[str], counts: dict) -> None:
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
            _walk_refs(value, f"{where}/{key}" if key != "$ref" else where, draft, base, errors, counts)
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            _walk_refs(value, f"{where}[{idx}]", draft, base, errors, counts)


def _endpoint_set(doc: dict) -> set[str]:
    return {
        f"{method.upper()} {path}"
        for path, item in doc["paths"].items()
        for method in item
        if method in METHODS
    }


def check(draft_path: Path = DRAFT, base_path: Path = BASE) -> tuple[dict, list[str]]:
    """返回 (计数, 错误列表)。错误为空即通过。"""

    draft = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    counts: dict[str, Any] = {}

    def bump(key: str, n: int = 1) -> None:
        counts[key] = counts.get(key, 0) + n

    _walk_refs(draft, "#", draft, base, errors, counts)

    # 1. 基本信息
    if draft.get("openapi") != "3.1.0":
        errors.append(f"openapi 版本不是 3.1.0：{draft.get('openapi')}")
    version = draft["info"]["version"]
    counts["version"] = version
    if version not in draft["info"]["description"]:
        errors.append(f"info.description 未提到 info.version（{version}），评审记录会混淆")

    # 2. 操作与 operationId
    ops: dict[str, dict] = {}
    for path, item in draft["paths"].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            oid = op.get("operationId")
            if not oid:
                errors.append(f"{method.upper()} {path} 缺 operationId")
                continue
            if oid in ops:
                errors.append(f"operationId 重复：{oid}")
            ops[oid] = {"path": path, "method": method, "op": op}
    counts["draft_operations"] = len(ops)

    # 3. security 只写身份，且 scheme 名必须已定义
    schemes = set((draft.get("components") or {}).get("securitySchemes") or {})
    if not schemes:
        errors.append("草案没有定义 securitySchemes")
    for oid, rec in ops.items():
        for requirement in rec["op"].get("security") or []:
            for name in requirement:
                bump("security_requirements")
                if name not in schemes:
                    errors.append(f"{oid}: security 引用了未定义的 scheme {name}")

    # 4. 每个操作都要有 x-e1-gate，且 local/restricted 都写了
    for oid, rec in ops.items():
        gate = rec["op"].get("x-e1-gate")
        if not isinstance(gate, dict):
            errors.append(f"{oid}: 缺 x-e1-gate")
            continue
        bump("gates")
        for mode in ("local", "restricted"):
            if mode not in gate:
                errors.append(f"{oid}: x-e1-gate 缺 {mode}")

    # 5. 登出：豁免门禁、无身份、无 401
    logout = ops.get("logoutUser")
    if logout is None:
        errors.append("缺 logoutUser")
    else:
        op = logout["op"]
        if op.get("security") != []:
            errors.append("logoutUser: security 必须为空列表")
        if (op.get("x-e1-gate") or {}).get("restricted") != "exempt":
            errors.append("logoutUser: restricted 门禁应为 exempt")
        if "401" in op.get("responses", {}):
            errors.append("logoutUser: 不应声明 401")

    # 6. 注册/登录必须写 Origin 参数
    for oid in ("registerUser", "loginUser"):
        rec = ops.get(oid)
        if rec is None:
            errors.append(f"缺 {oid}")
            continue
        refs = [p.get("$ref") for p in rec["op"].get("parameters") or [] if isinstance(p, dict)]
        if "#/components/parameters/OriginHeader" not in refs:
            errors.append(f"{oid}: 缺 OriginHeader 参数")

    # 7. 16 个既有操作逐项覆盖
    base_ops = _endpoint_set(base)
    impact = draft.get("x-e1-impact") or []
    impact_set = {row["endpoint"] for row in impact}
    counts["impact_rows"] = len(impact)
    counts["base_endpoints"] = len(base_ops)
    if impact_set != base_ops:
        errors.append(
            f"x-e1-impact 与基线端点不一致：多 {sorted(impact_set - base_ops)}，"
            f"少 {sorted(base_ops - impact_set)}"
        )
    for row in impact:
        for key in IMPACT_KEYS:
            if key not in row:
                errors.append(f"x-e1-impact {row['endpoint']} 缺 {key}")
        for mode in ("local", "restricted"):
            if mode not in (row.get("gate") or {}):
                errors.append(f"x-e1-impact {row['endpoint']} 的 gate 缺 {mode}")

    # 8. 错误码：唯一、被引用的都已定义、带身份的端点列出 401 族
    codes = draft.get("x-error-codes") or []
    code_names = [c["code"] for c in codes]
    counts["error_codes"] = len(code_names)
    if len(set(code_names)) != len(code_names):
        errors.append("x-error-codes 有重复 code")
    for row in impact:
        text = " ".join(str(item) for item in row["newFailures"])
        for token in CODE_RE.findall(text):
            if token not in code_names:
                errors.append(f"x-e1-impact {row['endpoint']}: 未定义错误码 {token}")
        needs_identity = str(row.get("identity", "无")).strip() not in ("无", "—", "")
        if needs_identity and "401" not in text:
            errors.append(
                f"x-e1-impact {row['endpoint']}: 该端点有身份要求，"
                "newFailures 必须列出 401 族（UNAUTHENTICATED/SESSION_INVALID/…）"
            )

    # 9. RunStatus 与生命周期扩展
    run_status = draft["components"]["schemas"]["RunStatus"]
    if run_status["enum"] != ["queued", "running", "succeeded", "failed"]:
        errors.append(f"RunStatus 枚举被改动：{run_status['enum']}")
    lifecycle = run_status.get("x-e1-lifecycle") or {}
    if lifecycle.get("reserved") != "interrupted":
        errors.append("RunStatus.x-e1-lifecycle.reserved 应为 interrupted")
    if "interrupted" in run_status["enum"]:
        errors.append("E1 的 RunStatus 不应包含 interrupted")

    # 10. 门禁模型：真值表覆盖两种模式，判定顺序每层都写了失败码
    model = draft.get("x-e1-gate-model") or {}
    table = model.get("gate-and-identity-truth-table") or []
    counts["truth_table_rows"] = len(table)
    modes_in_table = {row.get("mode") for row in table}
    if modes_in_table != {"local", "restricted"}:
        errors.append(f"真值表未覆盖两种模式：{modes_in_table}")
    counts["recipes"] = len([r for r in model.get("recipes") or [] if "name" in r])
    for step in model.get("evaluation-order") or []:
        if not {"step", "layer", "failure"} <= set(step):
            errors.append(f"evaluation-order 行缺字段：{step}")

    # 11. 请求/响应样例按 schema 校验（RefResolver 可跨文件）
    store: dict[str, dict] = {}
    for key in (
        draft_path.as_uri(),
        base_path.as_uri(),
        draft_path.as_posix(),
        base_path.as_posix(),
        "openapi.yaml",
        "./openapi.yaml",
    ):
        store[key] = draft if "e1-draft" in key else base

    with warnings.catch_warnings():
        # RefResolver 在 jsonschema 4.x 已弃用但仍是唯一稳定的跨文件方案
        warnings.simplefilter("ignore", DeprecationWarning)
        resolver = RefResolver(base_uri=draft_path.as_uri(), referrer=draft, store=store)
        for oid, rec in ops.items():
            op = rec["op"]
            for status, response in (op.get("responses") or {}).items():
                if not isinstance(response, dict) or "content" not in response:
                    continue
                for media in response["content"].values():
                    schema = media.get("schema")
                    if not schema:
                        continue
                    validator = Draft202012Validator(schema, resolver=resolver)
                    for name, example in (media.get("examples") or {}).items():
                        if "value" not in example:
                            continue
                        bump("response_examples")
                        for err in validator.iter_errors(example["value"]):
                            errors.append(
                                f"{oid} {status} example[{name}]: {err.message} @ {list(err.path)}"
                            )
            for media in ((op.get("requestBody") or {}).get("content") or {}).values():
                schema = media.get("schema")
                if not schema:
                    continue
                validator = Draft202012Validator(schema, resolver=resolver)
                for name, example in (media.get("examples") or {}).items():
                    if "value" not in example:
                        continue
                    bump("request_examples")
                    for err in validator.iter_errors(example["value"]):
                        errors.append(
                            f"{oid} request example[{name}]: {err.message} @ {list(err.path)}"
                        )

    return counts, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 contracts/openapi.e1-draft.yaml")
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
    print("\nOK：结构、引用、门禁、真值表、错误码与样例校验全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

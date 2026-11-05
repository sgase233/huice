"""评测数据集（CSV）与库里那几行之间的**逐条映射**。

整文件哈希只能证明「文件没变」，证明不了「这些文件就是导入进库的那一份」：拿两套
互不相关、但各自稳定的 CSV 与企业数据，哈希照样对得上。所以这里按内容把两边对齐：

* **政策版本**：CSV 的文号（``labels.csv`` 的 ``policy_version``、``criteria_review.csv``
  的 ``source_version``）↔ 库 ``policy_version.document_number``，没有文号时按 ``title``
  对齐（部分政策材料的「版本」本身是文件标题）。任一侧重复都算歧义，报出来。
* **条件**：政策版本内，CSV ``adjudicated_expression`` 的规范化 JSON 哈希 ↔ 库里该
  条件的 ``expressionFingerprint``。要求一对一：一条 CSV 行匹配到两行条件、或库里
  有条条件没有任何 CSV 行认领，都报出来。
* **企业**：CSV ``name_synthetic`` ↔ 库 ``enterprise.name_synthetic``。
* **事实**：在该企业下按 (key, 期间, unit, source, verified, 值哈希) 对齐；值按 CSV 的
  ``unit`` 解析成与库里同一类型的规范化值（金额/人数/比例用 Decimal，日期用 ISO，
  布尔用 true/false，代码用原字符串），再算同一个哈希。
* **标注用例**：``labels.csv`` 每行的 (企业代号, 文号, 条件代号) 必须都能经上面三条
  落到库里的具体行——这才是「标注的这份数据确实在库里」。

库里没有业务代号列：``policy_criterion.criterion_id``、``enterprise.id`` 都是导入时
生成的 UUID，CSV 的 ``CN-TAX-SME-2023-012`` / ``SME-001`` / ``E-ENT-001`` 在库里
没有对应字段。所以映射结果是一份**可核对的对照表**（代号 → 库里的 UUID），存进清单；
``verify`` 重新算一遍再比，两边对不上就不是同一份输入。

映射规则本身也有版本号（:data:`ALIGNMENT_RULES`）：改了对齐口径，旧清单在 ``verify``
里报「映射不同，可能是……对齐规则版本变了」，而不是含混地说「数据变了」。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .snapshot import SnapshotError, expression_fingerprint, sha256_hex

#: 对齐口径的版本号。改了规则就换一个值：旧清单会报「映射不同」，并点名可能是规则版本变了。
ALIGNMENT_RULES = "content-alignment@1"

#: 需要参与对齐的文件与它们的必需列。其余文件只算哈希，不做映射。
REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "labels.csv": ("case_id", "enterprise_id", "policy_version", "criterion_id"),
    "criteria_review.csv": (
        "policy_id",
        "criterion_id",
        "source_version",
        "adjudicated_expression",
    ),
    "enterprises.csv": (
        "enterprise_id",
        "name_synthetic",
        "fact_key",
        "fact_value",
        "unit",
        "period",
        "source",
        "verified",
    ),
}

#: 这些单位的 CSV 文本在库里是 ``numeric`` 列。
_DECIMAL_UNITS = frozenset({"CNY", "person", "ratio"})


def read_dataset_rows(path: Path) -> list[dict[str, str]]:
    """按 UTF-8（容忍 BOM）读一份 CSV；认识的表缺列直接报错，不猜列名。"""

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SnapshotError(f"数据集文件不是 UTF-8：{path.name}") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = [dict(row) for row in reader]
    required = REQUIRED_COLUMNS.get(path.name)
    if required:
        missing = [name for name in required if name not in (reader.fieldnames or [])]
        if missing:
            raise SnapshotError(f"{path.name} 缺少必需列：{', '.join(missing)}")
    return rows


def load_dataset_rows(
    directory: str | None, names: Sequence[str]
) -> dict[str, list[dict[str, str]]]:
    """读 CSV 的行内容；只读 :data:`REQUIRED_COLUMNS` 里认识的文件。"""

    base = Path(directory) if directory else Path.cwd()
    rows: dict[str, list[dict[str, str]]] = {}
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = base / path
        if path.name in REQUIRED_COLUMNS:
            rows[path.name] = read_dataset_rows(path)
    return rows


def _text(row: Mapping[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _parse_expression(text: str) -> Any:
    if not text:
        raise SnapshotError("criteria_review.csv 有行的 adjudicated_expression 为空")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise SnapshotError("criteria_review.csv 的 adjudicated_expression 不是合法 JSON") from None


def _parse_value(text: str, unit: str) -> Any:
    """把 CSV 的取值解析成与库里同一类型的值；空串是「未知」，与库里的空列对应。"""

    if text == "":
        return None
    if unit in _DECIMAL_UNITS:
        try:
            return Decimal(text)
        except InvalidOperation:
            raise SnapshotError(f"取值不是数字：{text!r}（unit {unit}）") from None
    if unit == "date":
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise SnapshotError(f"取值不是 ISO 日期：{text!r}") from None
    if unit == "boolean":
        lowered = text.lower()
        if lowered not in ("true", "false"):
            raise SnapshotError(f"取值不是布尔：{text!r}；写 true 或 false")
        return lowered == "true"
    return text


def _parse_period(text: str) -> tuple[str, str]:
    parts = text.split("/")
    if len(parts) != 2:
        raise SnapshotError(f"期间写法应为 起/止：{text!r}")
    return parts[0].strip(), parts[1].strip()


def _policy_index(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    """把政策版本按「文号 / 标题」编索引；一个标识对上多个版本算歧义。"""

    index: dict[str, list[Mapping[str, Any]]] = {}
    for policy in manifest.get("policies", []):
        for identifier in (policy.get("documentNumber"), policy.get("title")):
            text = (identifier or "").strip()
            if text:
                index.setdefault(text, []).append(policy)
    problems: list[str] = []
    unique: dict[str, Mapping[str, Any]] = {}
    for identifier, items in index.items():
        if len(items) > 1:
            versions = "、".join(f"{item.get('policyId')}@{item.get('version')}" for item in items)
            problems.append(f"标识 {identifier} 在库里有多个政策版本：{versions}")
        else:
            unique[identifier] = items[0]
    return unique, problems


def _align_criteria(
    rows: Sequence[Mapping[str, str]],
    policies: Mapping[str, Mapping[str, Any]],
    problems: list[str],
) -> dict[str, dict[str, str]]:
    """返回 ``{文号: {条件代号: 库条件 id}}``，同时把对不上的情况写进 ``problems``。"""

    mapping: dict[str, dict[str, str]] = {}
    claimed: dict[str, set[str]] = {}
    for row in rows:
        document = _text(row, "source_version")
        code = _text(row, "criterion_id")
        if not document or not code:
            problems.append("criteria_review.csv 有行缺少 source_version 或 criterion_id")
            continue
        policy = policies.get(document)
        if policy is None:
            problems.append(f"criteria_review.csv 的条件 {code}：库里没有文号/标题为 {document} 的政策")
            continue
        fingerprint = expression_fingerprint(
            _parse_expression(_text(row, "adjudicated_expression"))
        )
        matched = [
            item
            for item in policy.get("criteria", [])
            if item.get("expressionFingerprint") == fingerprint
        ]
        if not matched:
            problems.append(
                f"criteria_review.csv 的条件 {code}（{document}）在本版本库里找不到相同表达式"
            )
            continue
        if len(matched) > 1:
            problems.append(
                f"criteria_review.csv 的条件 {code}（{document}）匹配到库里 "
                f"{len(matched)} 条相同表达式，无法判定对应哪一条"
            )
            continue
        criterion_id = str(matched[0]["criterionId"])
        entry = mapping.setdefault(document, {})
        if code in entry and entry[code] != criterion_id:
            problems.append(f"criteria_review.csv 的条件代号 {code} 重复且指向不同条件")
            continue
        entry[code] = criterion_id
        claimed.setdefault(document, set()).add(criterion_id)

    # 反方向：库里这个版本有条条件，CSV 里没有任何一行认领它
    for document in sorted(mapping):
        policy = policies[document]
        unclaimed = [
            str(item["criterionId"])
            for item in policy.get("criteria", [])
            if str(item["criterionId"]) not in claimed.get(document, set())
        ]
        if unclaimed:
            problems.append(
                f"{document} 在库里有 {len(unclaimed)} 条条件未被 criteria_review.csv 覆盖"
            )
    return mapping


def _fact_key_of(fact: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        fact.get("key"),
        fact.get("periodStart"),
        fact.get("periodEnd"),
        fact.get("unit"),
        fact.get("source"),
        bool(fact.get("verified")),
        fact.get("valueHash"),
    )


def _align_enterprises(
    rows: Sequence[Mapping[str, str]],
    manifest: Mapping[str, Any],
    problems: list[str],
) -> dict[str, dict[str, Any]]:
    """返回 ``{企业代号: {code, nameSynthetic, enterpriseId}}``，并逐条核对事实。"""

    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for enterprise in manifest.get("enterprises", []):
        by_name.setdefault((enterprise.get("nameSynthetic") or "").strip(), []).append(enterprise)

    mapping: dict[str, dict[str, Any]] = {}
    claimed: dict[str, set[tuple[Any, ...]]] = {}
    for row in rows:
        code = _text(row, "enterprise_id")
        name = _text(row, "name_synthetic")
        if not code or not name:
            problems.append("enterprises.csv 有行缺少 enterprise_id 或 name_synthetic")
            continue
        candidates = by_name.get(name, [])
        if not candidates:
            problems.append(f"enterprises.csv 的企业 {code}（{name}）：库里没有同名企业")
            continue
        if len(candidates) > 1:
            problems.append(f"合成名称 {name} 在库里有多个企业，无法判定对应哪一个")
            continue
        enterprise = candidates[0]
        enterprise_id = str(enterprise["enterpriseId"])
        previous = mapping.get(code)
        if previous is not None and previous["enterpriseId"] != enterprise_id:
            problems.append(f"enterprises.csv 的企业代号 {code} 指向了多个企业")
            continue
        mapping[code] = {"code": code, "nameSynthetic": name, "enterpriseId": enterprise_id}

        fact_key = _text(row, "fact_key")
        if not fact_key:
            problems.append(f"enterprises.csv 的企业 {code} 有行缺少 fact_key")
            continue
        try:
            period_start, period_end = _parse_period(_text(row, "period"))
        except SnapshotError as exc:
            problems.append(f"enterprises.csv 的企业 {code} 的事实 {fact_key}：{exc}")
            continue
        unit = _text(row, "unit")
        try:
            value_hash = sha256_hex(_parse_value(_text(row, "fact_value"), unit))
        except SnapshotError as exc:
            problems.append(f"enterprises.csv 的企业 {code} 的事实 {fact_key}：{exc}")
            continue
        wanted = (
            fact_key,
            period_start,
            period_end,
            unit,
            _text(row, "source"),
            _text(row, "verified").lower() == "true",
            value_hash,
        )
        available = {_fact_key_of(fact) for fact in enterprise.get("facts", [])}
        if wanted not in available:
            problems.append(
                f"enterprises.csv 的企业 {code} 的事实 {fact_key}"
                f"（{period_start}..{period_end}）在库里对不上：键、期间、来源、"
                "核验状态或值有一项不同"
            )
            continue
        claimed.setdefault(code, set()).add(wanted)

    # 反方向：库里这个企业有条事实，CSV 里没有任何一行认领它
    by_id = {
        str(enterprise["enterpriseId"]): enterprise
        for enterprise in manifest.get("enterprises", [])
    }
    for code, entry in sorted(mapping.items()):
        enterprise = by_id[entry["enterpriseId"]]
        unclaimed = [
            fact
            for fact in enterprise.get("facts", [])
            if _fact_key_of(fact) not in claimed.get(code, set())
        ]
        if unclaimed:
            problems.append(
                f"企业 {code} 在库里有 {len(unclaimed)} 条事实未被 enterprises.csv 覆盖"
            )
    return mapping


def _align_cases(
    rows: Sequence[Mapping[str, str]],
    enterprises: Mapping[str, Mapping[str, Any]],
    criteria: Mapping[str, Mapping[str, str]],
    problems: list[str],
) -> int:
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        case_id = _text(row, "case_id")
        code = _text(row, "enterprise_id")
        document = _text(row, "policy_version")
        criterion = _text(row, "criterion_id")
        if code not in enterprises:
            problems.append(
                f"labels.csv 的用例 {case_id}：企业代号 {code} 不在 enterprises.csv 里"
            )
        if document not in criteria:
            problems.append(
                f"labels.csv 的用例 {case_id}：文号 {document} 不在 criteria_review.csv 里"
            )
        elif criterion not in criteria[document]:
            problems.append(
                f"labels.csv 的用例 {case_id}：条件 {criterion} 不在 {document} 的"
                " criteria_review.csv 里"
            )
        seen.add((case_id, code, document, criterion))
    return len(seen)


def align_dataset(
    datasets: Mapping[str, Sequence[Mapping[str, str]]],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """把 CSV 行与清单里的库侧条目逐条对齐，返回存进清单的映射与问题列表。"""

    problems: list[str] = []
    policies, policy_problems = _policy_index(manifest)
    problems.extend(policy_problems)

    criteria = _align_criteria(datasets.get("criteria_review.csv", []), policies, problems)
    enterprises = _align_enterprises(datasets.get("enterprises.csv", []), manifest, problems)
    case_count = _align_cases(datasets.get("labels.csv", []), enterprises, criteria, problems)

    return {
        "rules": ALIGNMENT_RULES,
        "files": sorted(datasets),
        "policies": [
            {
                "identifier": identifier,
                "policyVersionId": str(policies[identifier]["policyVersionId"]),
                "criteria": [
                    {"criterionCode": code, "criterionId": criterion_id}
                    for code, criterion_id in sorted(codes.items())
                ],
            }
            for identifier, codes in sorted(criteria.items())
        ],
        "enterprises": [dict(entry) for _, entry in sorted(enterprises.items())],
        "caseCount": case_count,
        "problems": sorted(set(problems)),
    }

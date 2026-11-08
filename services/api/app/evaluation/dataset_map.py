"""评测数据集（CSV）与库里那几行之间的**逐条映射**。

整文件哈希只能证明「文件没变」，证明不了「这些文件就是导入进库的那一份」：拿两套
互不相关、但各自稳定的 CSV 与企业数据，哈希照样对得上。所以这里按内容把两边对齐：

* **政策版本**：靠 ``source_register.csv`` 的 ``policy_id → 版本`` 登记判定，并与 CSV
  的 ``policy_version``／``source_version``、库里的 ``document_number``／``title``
  三方核对。``source_version`` 不一定是本政策的文号：数据侧也用它记这条引文实际引自
  哪份文件（如《财税〔2015〕119号》、申报通知）。归属**不靠**「表达式在库里唯一」——
  另一份政策恰好有唯一相同表达式时，那不能证明这条条件属于它；归属只由 ``policy_id``
  经登记表决定。``source_version`` 恰好是另一份已登记政策的文号时直接报未解决。
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
#: ``@2``：``source_version`` 不是本政策文号时（记的是这条引文实际引自的文件），
#: 退回按表达式指纹找唯一归属；映射键改用库里的文号/标题，不再原样回填 CSV 那一列。
#: ``@3``：归属不再靠「表达式在库里唯一」，而是由 ``source_register.csv`` 的
#: ``policy_id → 版本`` 判定，并与库里的文号/标题三方核对；同时要求条件一对一
#: （同一条库条件不能被两个 CSV 条件代号认领）。
ALIGNMENT_RULES = "content-alignment@3"

#: 需要参与对齐的文件与它们的必需列。其余文件只算哈希，不做映射。
REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "labels.csv": (
        "case_id",
        "enterprise_id",
        "policy_id",
        "policy_version",
        "criterion_id",
    ),
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
    # 政策代号 → 政策版本（文号/标题）的登记表。CSV 的业务代号在库里没有对应列，
    # 「这条条件属于哪份政策」只能靠它约束；没有它，表达式指纹唯一也不足以证明归属。
    "source_register.csv": ("policy_id", "version", "title"),
}

#: 正式评测冻结必须齐的四份输入；缺任何一份，映射就证明不了整体对应关系。
REQUIRED_FILES: tuple[str, ...] = tuple(REQUIRED_COLUMNS)

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


def load_dataset_rows(paths: Sequence[Path]) -> dict[str, list[dict[str, str]]]:
    """读 CSV 的行内容；只读 :data:`REQUIRED_COLUMNS` 里认识的文件。

    键是**文件名**（对齐规则按它区分四份输入，``labels.csv`` 就是标注表），所以调用方
    必须先保证没有两个不同目录的同名文件——那会让这里的键互相覆盖。
    """

    rows: dict[str, list[dict[str, str]]] = {}
    for path in paths:
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


def _policy_identifier(policy: Mapping[str, Any]) -> str:
    """库里用来跟数据集对齐的标识：优先文号，没有文号用标题（与 :func:`_policy_index` 同口径）。"""

    return (
        (policy.get("documentNumber") or "").strip()
        or (policy.get("title") or "").strip()
    )


def _criteria_by_fingerprint(
    manifest: Mapping[str, Any],
) -> dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]]:
    """表达式指纹 → 库里所有含该表达式的 (政策版本, 条件)。

    只用来在「找不到」时解释这条表达式其实在哪儿，**不用来定归属**：另一份政策恰好有
    唯一相同的表达式，也不能证明这条条件属于它（M2 在 #51 复审指出）。
    """

    index: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for policy in manifest.get("policies", []):
        for item in policy.get("criteria", []):
            fingerprint = item.get("expressionFingerprint")
            if fingerprint:
                index.setdefault(str(fingerprint), []).append((policy, item))
    return index


def _register_versions(
    rows: Sequence[Mapping[str, str]],
    problems: list[str],
) -> dict[str, list[dict[str, str]]]:
    """``policy_id → [{version, title}]``：受签认的政策代号登记表。

    CSV 的业务代号在库里没有列，唯一能把「这条条件属于哪份政策」写死的就是这张表。
    """

    register: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        policy_id = _text(row, "policy_id")
        version = _text(row, "version")
        title = _text(row, "title")
        if not policy_id:
            problems.append("source_register.csv 有行缺少 policy_id")
            continue
        if not version and not title:
            problems.append(
                f"source_register.csv 的 {policy_id} 既没有 version 也没有 title，"
                "无法对应到库里的政策版本"
            )
            continue
        register.setdefault(policy_id, []).append({"version": version, "title": title})
    for policy_id, items in register.items():
        versions = [item["version"] for item in items if item["version"]]
        duplicates = sorted({value for value in versions if versions.count(value) > 1})
        for version in duplicates:
            problems.append(
                f"source_register.csv 的政策代号 {policy_id} 重复登记了版本 {version}，无法判定"
            )
    return register


def _register_owners(
    register: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, set[str]]:
    """版本的文号/标题 → 登记在哪个 ``policy_id`` 名下，用来发现「引文文号属于别的政策」。"""

    owners: dict[str, set[str]] = {}
    for policy_id, items in register.items():
        for item in items:
            for identifier in (item.get("version"), item.get("title")):
                text = (identifier or "").strip()
                if text:
                    owners.setdefault(text, set()).add(policy_id)
    return owners


def _matches_version(item: Mapping[str, str], text: str) -> bool:
    return bool(text) and text in (
        (item.get("version") or "").strip(),
        (item.get("title") or "").strip(),
    )


def _pick_registered_version(
    policy_id: str,
    source_version: str,
    register: Mapping[str, Sequence[Mapping[str, str]]],
    owners: Mapping[str, set[str]],
    problems: list[str],
) -> Mapping[str, str] | None:
    """按 ``policy_id`` 与 ``source_version`` 定版本；定不下来就报未解决，不猜。"""

    items = register.get(policy_id)
    if not items:
        problems.append(
            f"criteria_review.csv 的政策代号 {policy_id} 没有在 source_register.csv 登记，"
            "条件的政策归属无法核对"
        )
        return None
    matched = [item for item in items if _matches_version(item, source_version)]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        problems.append(
            f"source_version {source_version} 同时对上了政策代号 {policy_id} 在登记表里的"
            "多个版本，无法判定条件属于哪一版"
        )
        return None
    others = sorted(owners.get(source_version, set()) - {policy_id})
    if others:
        problems.append(
            f"criteria_review.csv 里政策代号 {policy_id} 的行写着 source_version "
            f"{source_version}，而它登记在另一份政策（{'、'.join(others)}）名下——"
            "来源文号与政策代号不一致"
        )
        return None
    if len(items) > 1:
        versions = "、".join(item["version"] or item["title"] for item in items)
        problems.append(
            f"政策代号 {policy_id} 在登记表里有 {len(items)} 个版本（{versions}），"
            f"source_version {source_version} 不是其中任何一个，无法判定条件属于哪一版"
        )
        return None
    # 只有一个版本：这一列记的是登记表之外的文件（如《财税〔2015〕119号》、申报通知），
    # 归属仍由 policy_id 决定，内容还要在该版本里逐条比对。
    return items[0]


def _resolve_library_version(
    policy_id: str,
    item: Mapping[str, str],
    policies: Mapping[str, Mapping[str, Any]],
    problems: list[str],
) -> tuple[Mapping[str, Any], str] | None:
    """把登记表的一行落到库里唯一一个政策版本，并核对文号/标题与库一致。"""

    version = (item.get("version") or "").strip()
    title = (item.get("title") or "").strip()
    policy = policies.get(version) if version else None
    if policy is None and title:
        policy = policies.get(title)
    if policy is None:
        problems.append(
            f"source_register.csv 的 {policy_id}@{version or title} 在库里没有对应版本"
        )
        return None
    library_version = (policy.get("documentNumber") or "").strip()
    library_title = (policy.get("title") or "").strip()
    if version and library_version and version != library_version:
        problems.append(
            f"政策代号 {policy_id}：登记表版本是 {version}，库里这个版本的文号是 "
            f"{library_version}，两者不一致"
        )
        return None
    if title and library_title and title != library_title:
        problems.append(
            f"政策代号 {policy_id}@{version or title}：登记表标题与库里标题不一致"
        )
        return None
    identifier = _policy_identifier(policy)
    if not identifier:
        problems.append(
            f"政策代号 {policy_id}@{version or title} 对上的政策版本没有文号也没有标题"
        )
        return None
    return policy, identifier


def _align_criteria(
    rows: Sequence[Mapping[str, str]],
    register: Mapping[str, Sequence[Mapping[str, str]]],
    policies: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    problems: list[str],
) -> dict[str, dict[str, str]]:
    """返回 ``{文号: {条件代号: 库条件 id}}``，同时把对不上的情况写进 ``problems``。

    映射的键是**库里的标识**（``document_number``，没有时用标题），不是 CSV 那一列：
    ``labels.csv`` 是按文号找条件的，键若原样回填 ``source_version``，引用了别份文件的
    行就会在映射里挂到一个库里不存在文号下，标注用例跟着全部对不上。
    """

    by_fingerprint = _criteria_by_fingerprint(manifest)
    owners = _register_owners(register)
    mapping: dict[str, dict[str, str]] = {}
    resolved: dict[str, tuple[str, Mapping[str, Any]]] = {}
    claimants: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        policy_id = _text(row, "policy_id")
        code = _text(row, "criterion_id")
        source_version = _text(row, "source_version")
        if not policy_id or not code:
            problems.append("criteria_review.csv 有行缺少 policy_id 或 criterion_id")
            continue
        fingerprint = expression_fingerprint(
            _parse_expression(_text(row, "adjudicated_expression"))
        )
        item = _pick_registered_version(policy_id, source_version, register, owners, problems)
        if item is None:
            continue
        found = _resolve_library_version(policy_id, item, policies, problems)
        if found is None:
            continue
        policy, identifier = found
        matched = [
            criterion
            for criterion in policy.get("criteria", [])
            if criterion.get("expressionFingerprint") == fingerprint
        ]
        if not matched:
            elsewhere = sorted(
                {str(entry[0].get("policyVersionId")) for entry in by_fingerprint.get(fingerprint, [])}
            )
            hint = (
                f"；该表达式在库里的 {'、'.join(elsewhere)} 出现，但没有登记给 {policy_id}"
                if elsewhere
                else ""
            )
            problems.append(
                f"criteria_review.csv 的条件 {code}（{policy_id}"
                f"@{item['version'] or item['title']}）在库里找不到相同表达式{hint}"
            )
            continue
        if len(matched) > 1:
            problems.append(
                f"criteria_review.csv 的条件 {code}（{policy_id}@{item['version'] or item['title']}）"
                f"在该政策版本里有 {len(matched)} 条相同表达式，无法判定对应哪一条"
            )
            continue
        criterion_id = str(matched[0]["criterionId"])
        entry = mapping.setdefault(identifier, {})
        previous = entry.get(code)
        if previous is not None and previous != criterion_id:
            problems.append(f"criteria_review.csv 的条件代号 {code} 重复且指向不同条件")
            continue
        entry[code] = criterion_id
        version_id = str(policy.get("policyVersionId"))
        resolved.setdefault(version_id, (identifier, policy))
        claimants.setdefault((version_id, criterion_id), set()).add(code)

    # 一对一：同一条库条件不能被两个 CSV 条件代号同时认领
    for (version_id, criterion_id), codes in sorted(claimants.items()):
        if len(codes) > 1:
            identifier = resolved[version_id][0]
            problems.append(
                f"{identifier} 的库条件 {criterion_id} 被 {len(codes)} 个 CSV 条件代号认领"
                f"（{'、'.join(sorted(codes))}），条件映射不是一对一"
            )

    # 反方向：库里这个版本有条条件，CSV 里没有任何一行认领它
    for version_id, (identifier, policy) in sorted(resolved.items()):
        claimed = {cid for (vid, cid) in claimants if vid == version_id}
        unclaimed = [
            str(item["criterionId"])
            for item in policy.get("criteria", [])
            if str(item["criterionId"]) not in claimed
        ]
        if unclaimed:
            problems.append(
                f"{identifier} 在库里有 {len(unclaimed)} 条条件未被 criteria_review.csv 覆盖"
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
            value = _parse_value(_text(row, "fact_value"), unit)
        except SnapshotError as exc:
            problems.append(f"enterprises.csv 的企业 {code} 的事实 {fact_key}：{exc}")
            continue
        # 未知值在清单里就是 ``valueHash: null``（见 ``snapshot._fact_entry``），
        # 不能先哈希 None——那会得到一个字符串哈希，和库里的空值永远对不上
        value_hash = None if value is None else sha256_hex(value)
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
    register: Mapping[str, Sequence[Mapping[str, str]]],
    problems: list[str],
) -> int:
    seen: set[tuple[str, str, str, str]] = set()
    checked: set[tuple[str, str]] = set()
    for row in rows:
        case_id = _text(row, "case_id")
        code = _text(row, "enterprise_id")
        policy_id = _text(row, "policy_id")
        document = _text(row, "policy_version")
        criterion = _text(row, "criterion_id")
        if code not in enterprises:
            problems.append(
                f"labels.csv 的用例 {case_id}：企业代号 {code} 不在 enterprises.csv 里"
            )
        # 三方一致：policy_id 经登记表指向的版本，必须就是这一行写的 policy_version。
        # 只比其中一列，标签挂到另一份政策的同代号条件下也会「对得上」。
        if not policy_id or not document:
            problems.append(
                f"labels.csv 的用例 {case_id}：缺少 policy_id 或 policy_version，无法核对归属"
            )
        elif (policy_id, document) not in checked:
            checked.add((policy_id, document))
            items = register.get(policy_id, [])
            if not items:
                problems.append(
                    f"labels.csv 的用例 {case_id}：政策代号 {policy_id} 不在 "
                    "source_register.csv 里"
                )
            elif not any(_matches_version(item, document) for item in items):
                versions = "、".join(item["version"] or item["title"] for item in items)
                problems.append(
                    f"labels.csv 的用例 {case_id}：政策代号 {policy_id} 在登记表里登记的是"
                    f" {versions}，policy_version 却写着 {document}——标签的政策归属不一致"
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
    *,
    files: Sequence[str] | None = None,
) -> dict[str, Any]:
    """把 CSV 行与清单里的库侧条目逐条对齐，返回存进清单的映射与问题列表。

    ``files`` 是这几份 CSV 的**签认路径**（命令行传进来），目录不同也能分辨；不传时
    退回文件名——直接调用本函数的测试不需要路径，但写清单的 CLI 必须传。
    """

    problems: list[str] = []
    policies, policy_problems = _policy_index(manifest)
    problems.extend(policy_problems)

    provided = set(datasets)
    # 只对上了一份、或一份都没给，都不算「映射做完了」：正式冻结要四份齐全
    for name in REQUIRED_FILES:
        if name not in provided:
            problems.append(f"数据集映射未解决：没有加载 {name} 的行内容")

    register = _register_versions(datasets.get("source_register.csv", []), problems)
    criteria = _align_criteria(
        datasets.get("criteria_review.csv", []), register, policies, manifest, problems
    )
    enterprises = _align_enterprises(datasets.get("enterprises.csv", []), manifest, problems)
    case_count = _align_cases(
        datasets.get("labels.csv", []), enterprises, criteria, register, problems
    )

    # 「文件给了但一条都没对上」同样是没做完：空映射不能当作核对通过
    if "source_register.csv" in provided and not register:
        problems.append("数据集映射未解决：没有任何政策代号被登记到库里的版本")
    if "criteria_review.csv" in provided and not criteria:
        problems.append("数据集映射未解决：没有任何条件被对齐到库里的条件")
    if "enterprises.csv" in provided and not enterprises:
        problems.append("数据集映射未解决：没有任何企业对得上库里的企业")
    if "labels.csv" in provided and case_count == 0:
        problems.append("数据集映射未解决：labels.csv 没有可用的标注用例")

    return {
        "rules": ALIGNMENT_RULES,
        "files": sorted(datasets if files is None else files),
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

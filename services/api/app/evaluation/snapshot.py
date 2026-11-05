"""评测输入快照：让「签认的那份数据」与「库里真正跑的那份数据」能对上。

``docs/product/EVALUATION_FREEZE.md`` §5.2 要求执行前给出可核对的输入映射。三份 CSV
的哈希只能识别文件，说明不了这些文件是否就是导入进库的那一份——所以这里从**数据库
本身**导出映射，再让核对命令拿同一份清单回查。

三件事：

* **规范化哈希**：条件表达式（AST）与事实值各给一个稳定可复现的 SHA-256。键序、
  ``Decimal`` 写法、日期格式都不该影响结果；值变了一定要变。
* **输入清单**：政策版本 → 条件、企业 → 事实；事实版本直接复用引擎的
  :func:`app.agents.profile.fact_version_of`，保证清单里的版本号与运行记录里的
  是同一个算法算出来的。
* **一致性核对**：拿清单对当前库重算一遍，逐项列出差异。不一致就停，**不用运行结果
  反推快照**。

配置哈希只覆盖**非敏感**项（见 :data:`CONFIG_FINGERPRINT_KEYS`）。口令、令牌、密钥、
连接串永远不进哈希输入——否则这份要贴进报告的清单本身就成了密钥派生物。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from ..agents.profile import NormalizedFact, fact_version_of
from ..config import APP_VERSION
from ..domain.rules import RULE_VERSION

#: 清单结构版本；字段口径变了才动它，改代码不改口径别动。
MANIFEST_VERSION = "1"

#: 进配置哈希的键。只列非敏感项——名字里带令牌/密钥/连接串的一律不在此表内，
#: :func:`config_fingerprint` 还会再挡一次。
CONFIG_FINGERPRINT_KEYS: tuple[str, ...] = (
    "APP_ENV",
    "DEMO_ACCESS_MODE",
    "MODEL_MODE",
    "DEEPSEEK_MODEL",
    "OBJECT_STORE_MODE",
)

#: 即便有人把它们加进上表也拒绝计算：这些值一旦进哈希，清单就成了密钥派生物。
_FORBIDDEN_CONFIG_FRAGMENTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "DATABASE_URL", "REDIS_URL")


class SnapshotError(Exception):
    """清单无法按要求生成（配置项不合法、库结构与预期不符等）。"""


# ---------------------------------------------------------------- 规范化与哈希


def _canonical_value(value: Any) -> Any:
    """把常见类型转成 JSON 可表示、且跨端一致的形式。"""

    if isinstance(value, Decimal):
        # 3.10 与 3.1 是同一个数；``normalize`` 去掉尾零后统一写成普通小数
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        # 库里取出的 timestamptz 一定带时区；手工传入的 naive 值按 UTC 处理，
        # 不能在这里抛异常——那会让整份清单因为一个时间戳而生成失败。
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    """稳定序列化：排序键、无多余空白、中文按 UTF-8 原样（不转义）。"""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def expression_fingerprint(expression: Mapping[str, Any]) -> str:
    """条件表达式的 AST 哈希。

    与引文哈希（``domain.citations.quote_hash``）不是一回事：那个标识原文片段，
    这个标识结构化条件本身，键序不同、写法不同的等价表达式应得到同一个值。
    """

    if not isinstance(expression, Mapping):
        raise SnapshotError("条件表达式必须是对象")
    return sha256_hex(expression)


def config_fingerprint(config: Mapping[str, str]) -> str:
    """非敏感配置的规范化哈希；缺项记为 ``null``，让「没配」与「配了空」可区分。"""

    selected: dict[str, Any] = {}
    for name in CONFIG_FINGERPRINT_KEYS:
        upper = name.upper()
        if any(fragment in upper for fragment in _FORBIDDEN_CONFIG_FRAGMENTS):
            raise SnapshotError(f"配置项 {name} 属于敏感值，不能进配置哈希")
        selected[name] = config.get(name)
    return sha256_hex(selected)


def _fact_of(row: Mapping[str, Any]) -> NormalizedFact:
    """把 ``enterprise_fact`` 的一行还原成引擎认的形状（值列四选一，全空即未知）。"""

    value: Decimal | date | str | bool | None
    if row["value_code"] is not None:
        value = row["value_code"]
    elif row["value_date"] is not None:
        value = row["value_date"]
    elif row["value_amount"] is not None:
        value = row["value_amount"]
    elif row["value_boolean"] is not None:
        value = row["value_boolean"]
    else:
        value = None
    return NormalizedFact(
        key=row["key"],
        unit=row["unit"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        source=row["source"],
        value=value,
    )


def _fact_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    fact = _fact_of(row)
    return {
        "key": fact.key,
        "periodStart": fact.period_start.isoformat(),
        "periodEnd": fact.period_end.isoformat(),
        "unit": fact.unit,
        "source": fact.source,
        "verified": bool(row["verified"]),
        # 值本身不进清单（可能含企业材料里的敏感判断），只给哈希；未知值记 null
        "valueHash": None if fact.value is None else sha256_hex(fact.value),
    }


# ---------------------------------------------------------------- 从库里导出


_POLICY_SQL = """
SELECT pv.id            AS policy_version_id,
       pv.policy_id     AS policy_id,
       pv.version       AS version,
       pv.title         AS title,
       pv.policy_type   AS policy_type,
       pv.review_status AS review_status,
       pv.imported_at   AS imported_at,
       pv.revision      AS revision
  FROM policy_version pv
 ORDER BY pv.policy_id, pv.version
"""

_CRITERION_SQL = """
SELECT pc.policy_version_id  AS policy_version_id,
       pc.criterion_id       AS criterion_id,
       pc.title              AS title,
       pc.expression         AS expression,
       pc.review_finding     AS review_finding,
       pc.validation_status  AS validation_status
  FROM policy_criterion pc
 ORDER BY pc.policy_version_id, pc.criterion_id
"""

_ENTERPRISE_SQL = """
SELECT e.id             AS enterprise_id,
       e.name_synthetic AS name_synthetic,
       e.period_start   AS period_start,
       e.period_end     AS period_end
  FROM enterprise e
 ORDER BY e.id
"""

_FACT_SQL = """
SELECT f.enterprise_id AS enterprise_id,
       f.key           AS key,
       f.period_start  AS period_start,
       f.period_end    AS period_end,
       f.unit          AS unit,
       f.source        AS source,
       f.verified      AS verified,
       f.value_code    AS value_code,
       f.value_date    AS value_date,
       f.value_amount  AS value_amount,
       f.value_boolean AS value_boolean
  FROM enterprise_fact f
 ORDER BY f.enterprise_id, f.key, f.period_start, f.period_end
"""


def _rows(conn: Connection, sql: str) -> list[Mapping[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def collect_input_snapshot(
    conn: Connection,
    *,
    config: Mapping[str, str] | None = None,
    dataset: Mapping[str, str] | None = None,
    code_commit: str | None = None,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    """导出当前库的输入映射。

    ``config`` 传的是**运行进程的环境**（部署时由 systemd 注入）；不传就只记键名、
    配置哈希为 ``null``——与其猜，不如显式标「没提供」。

    ``dataset`` 是 ``{文件名: sha256}``，由调用方按文件字节算好；``code_commit``
    在云端（没有 ``.git``）拿不到，就留 ``null``，别编一个。
    """

    criteria_by_version: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(conn, _CRITERION_SQL):
        criteria_by_version.setdefault(str(row["policy_version_id"]), []).append(
            {
                "criterionId": str(row["criterion_id"]),
                "title": row["title"],
                "expressionFingerprint": expression_fingerprint(row["expression"]),
                "reviewFinding": row["review_finding"],
                "validationStatus": row["validation_status"],
            }
        )

    policies: list[dict[str, Any]] = []
    for row in _rows(conn, _POLICY_SQL):
        version_id = str(row["policy_version_id"])
        criteria = sorted(
            criteria_by_version.get(version_id, []), key=lambda item: item["criterionId"]
        )
        policies.append(
            {
                "policyId": str(row["policy_id"]),
                "policyVersionId": version_id,
                "version": row["version"],
                "title": row["title"],
                "policyType": row["policy_type"],
                "reviewStatus": row["review_status"],
                "importedAt": _canonical_value(row["imported_at"]),
                "revision": str(row["revision"]),
                "criteria": criteria,
                # 条件集合的指纹：覆盖「哪条 + 什么表达式」，id 换了也算变
                "criteriaSetFingerprint": sha256_hex(
                    [
                        {"criterionId": item["criterionId"], "fingerprint": item["expressionFingerprint"]}
                        for item in criteria
                    ]
                ),
            }
        )

    facts_by_enterprise: dict[str, list[NormalizedFact]] = {}
    fact_entries: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(conn, _FACT_SQL):
        enterprise_id = str(row["enterprise_id"])
        facts_by_enterprise.setdefault(enterprise_id, []).append(_fact_of(row))
        fact_entries.setdefault(enterprise_id, []).append(_fact_entry(row))

    enterprises: list[dict[str, Any]] = []
    for row in _rows(conn, _ENTERPRISE_SQL):
        enterprise_id = str(row["enterprise_id"])
        facts = facts_by_enterprise.get(enterprise_id, [])
        enterprises.append(
            {
                "enterpriseId": enterprise_id,
                "nameSynthetic": row["name_synthetic"],
                "period": {
                    "startDate": row["period_start"].isoformat(),
                    "endDate": row["period_end"].isoformat(),
                },
                # 与 assessment_run.fact_version 同一个算法
                "factVersion": fact_version_of(facts),
                "facts": sorted(
                    fact_entries.get(enterprise_id, []),
                    key=lambda item: (item["key"], item["periodStart"], item["periodEnd"]),
                ),
            }
        )

    stamp = collected_at or datetime.now(timezone.utc)
    return {
        "manifestVersion": MANIFEST_VERSION,
        "collectedAt": _canonical_value(stamp),
        # 运行参数里不随库变化的部分（§5.2 第 3 项）
        "appVersion": APP_VERSION,
        "ruleVersion": RULE_VERSION,
        "codeCommit": code_commit,
        "dataset": None if dataset is None else dict(sorted(dataset.items())),
        "configKeys": list(CONFIG_FINGERPRINT_KEYS),
        "configFingerprint": None if config is None else config_fingerprint(config),
        "policies": policies,
        "enterprises": enterprises,
    }


# ---------------------------------------------------------------- 一致性核对


def _index(items: Iterable[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    return {str(item[key]): item for item in items}


def diff_snapshots(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """逐项对比两份清单，返回人能读的差异列表；空列表表示一致。

    ``expected`` 是签认/归档的那份，``actual`` 是刚对当前库重算的那份。只比较内容，
    不比较 ``collectedAt``（采集时间天然不同）。
    """

    problems: list[str] = []
    if expected.get("manifestVersion") != actual.get("manifestVersion"):
        problems.append(
            f"清单版本不同：签认 {expected.get('manifestVersion')!r}，"
            f"当前 {actual.get('manifestVersion')!r}"
        )

    for field, label in (
        ("appVersion", "服务版本"),
        ("ruleVersion", "规则版本"),
        ("configFingerprint", "配置哈希"),
        ("codeCommit", "代码提交"),
    ):
        before, now = expected.get(field), actual.get(field)
        if before != now:
            problems.append(f"{label}不同：签认 {before!r}，当前 {now!r}")

    # 只核对签认时列出的文件：后来往目录里加了别的 CSV 不影响已签认的输入，
    # 但签认过的文件改了或没了，必须报出来。
    before_dataset = expected.get("dataset") or {}
    now_dataset = actual.get("dataset") or {}
    for name in sorted(before_dataset):
        if name not in now_dataset:
            problems.append(f"数据集文件缺失：{name}")
        elif before_dataset[name] != now_dataset[name]:
            problems.append(f"数据集文件 {name} 的哈希不同")

    _diff_policies(expected.get("policies", []), actual.get("policies", []), problems)
    _diff_enterprises(expected.get("enterprises", []), actual.get("enterprises", []), problems)
    return problems


def _diff_policies(
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    problems: list[str],
) -> None:
    def key_of(item: Mapping[str, Any]) -> str:
        return f"{item['policyId']}@{item['version']}"

    want = {key_of(item): item for item in expected}
    have = {key_of(item): item for item in actual}

    for name in sorted(want.keys() - have.keys()):
        problems.append(f"政策版本缺失：{name}（签认时有，库里没有）")
    for name in sorted(have.keys() - want.keys()):
        problems.append(f"政策版本多出：{name}（库里有，签认时没有）")

    for name in sorted(want.keys() & have.keys()):
        before, now = want[name], have[name]
        if str(before.get("policyVersionId")) != str(now.get("policyVersionId")):
            problems.append(f"{name}：policyVersionId 不同")
        if before.get("criteriaSetFingerprint") != now.get("criteriaSetFingerprint"):
            problems.append(f"{name}：条件集合指纹不同")
        # 指纹只覆盖「哪条 + 什么表达式」；总是逐条比一次，才不会漏掉标题这类变化
        _diff_criteria(name, before.get("criteria", []), now.get("criteria", []), problems)


def _diff_criteria(
    name: str,
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    problems: list[str],
) -> None:
    want = _index(expected, "criterionId")
    have = _index(actual, "criterionId")
    for criterion in sorted(want.keys() - have.keys()):
        problems.append(f"{name}：条件缺失 {criterion}")
    for criterion in sorted(have.keys() - want.keys()):
        problems.append(f"{name}：条件多出 {criterion}")
    for criterion in sorted(want.keys() & have.keys()):
        before, now = want[criterion], have[criterion]
        if before.get("expressionFingerprint") != now.get("expressionFingerprint"):
            problems.append(f"{name}：条件 {criterion} 的表达式变了")
        if before.get("title") != now.get("title"):
            problems.append(f"{name}：条件 {criterion} 的标题变了")
        if before.get("validationStatus") != now.get("validationStatus"):
            problems.append(f"{name}：条件 {criterion} 的校验状态变了")


def _diff_enterprises(
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    problems: list[str],
) -> None:
    want = _index(expected, "enterpriseId")
    have = _index(actual, "enterpriseId")
    for enterprise in sorted(want.keys() - have.keys()):
        problems.append(f"企业缺失：{enterprise}")
    for enterprise in sorted(have.keys() - want.keys()):
        problems.append(f"企业多出：{enterprise}")
    for enterprise in sorted(want.keys() & have.keys()):
        before, now = want[enterprise], have[enterprise]
        if before.get("factVersion") != now.get("factVersion"):
            problems.append(f"企业 {enterprise}：事实版本不同")
        # 逐条比一遍：事实版本只是汇总，漏了哪条、值怎么变的要能说清楚
        _diff_facts(enterprise, before.get("facts", []), now.get("facts", []), problems)


def _diff_facts(
    enterprise: str,
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    problems: list[str],
) -> None:
    def key_of(item: Mapping[str, Any]) -> str:
        return f"{item['key']}@{item['periodStart']}..{item['periodEnd']}"

    want = {key_of(item): item for item in expected}
    have = {key_of(item): item for item in actual}
    for fact in sorted(want.keys() - have.keys()):
        problems.append(f"企业 {enterprise}：事实缺失 {fact}")
    for fact in sorted(have.keys() - want.keys()):
        problems.append(f"企业 {enterprise}：事实多出 {fact}")
    for fact in sorted(want.keys() & have.keys()):
        if want[fact].get("valueHash") != have[fact].get("valueHash"):
            problems.append(f"企业 {enterprise}：事实 {fact} 的值变了")

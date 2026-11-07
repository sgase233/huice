"""材料要求 CSV 的解析与校验（纯函数，不碰数据库）。

口径来自 `docs/product/MATERIAL_REQUIREMENTS.md`（M3，唯一依据）与 #37 的仲裁结论：

* 只导入 ``review_status`` 恰为 **已仲裁** 的行；`待核实（依据未入库）` 的行不导入——
  它们仍可用于人工查阅，但不得生成可发布条目，也不得据此置 ``verified=true``。
  判据只读这一列，不硬编码行号或材料 ID：仲裁状态变了，导入集合跟着变。
* ``required`` 只接受 ``必交`` / ``条件交``。必交不留触发条件，条件交必须有——
  否则展示层说不清「什么时候才需要这份材料」。
* ``proves_fact_keys`` 只能取事实字典里的键；空表示这份材料不驱动 ``verified``
  （如申请书），运行期它的状态是「需人工核对」而不是「资料缺失」。
* ``applies_to_policy_ids`` 是 ``source_register.csv`` 的业务代号；留空表示该
  ``policy_type`` 下全部政策适用。解析成库里的政策 UUID 在导入器里做，不在本模块。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: CSV 表头（顺序固定，M1 导入时映射 camelCase，字段名不得任意改）。
COLUMNS: tuple[str, ...] = (
    "policy_type",
    "material_id",
    "title",
    "required",
    "condition_desc",
    "description",
    "proves_fact_keys",
    "applies_to_policy_ids",
    "source_basis",
    "review_status",
)

#: 只有复核状态恰为此值的行可以导入。
ARBITRATED = "已仲裁"

REQUIRED_TRUE = "必交"
REQUIRED_FALSE = "条件交"

_MATERIAL_ID = re.compile(r"^(TAX|GRANT)-[0-9]{2}$")
_POLICY_TYPES = ("tax", "grant")


class MaterialError(Exception):
    """CSV 不符合口径；消息直接面向执行导入的人，点出行号与原因。"""


@dataclass(frozen=True, slots=True)
class MaterialRow:
    """一份材料要求。字段与 CSV 列一一对应，只在类型上归一（布尔、元组）。"""

    material_id: str
    policy_type: str
    title: str
    is_required: bool
    condition_desc: str | None
    description: str
    proves_fact_keys: tuple[str, ...]
    applies_to_policy_codes: tuple[str, ...]
    source_basis: str
    review_status: str


@dataclass(frozen=True, slots=True)
class Catalog:
    """一次解析的结果：可导入的行 + 按状态被跳过的行（附原因，便于回告）。"""

    importable: tuple[MaterialRow, ...]
    skipped: tuple[tuple[str, str], ...]

    def review_statuses(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.importable:
            counts[row.review_status] = counts.get(row.review_status, 0) + 1
        for _, status in self.skipped:
            counts[status] = counts.get(status, 0) + 1
        return counts


def _text(row: Mapping[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _split(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(";") if part.strip())


def read_rows(path: Path) -> list[dict[str, str]]:
    """按 UTF-8（容忍 BOM）读 CSV；缺列直接报错，不去猜列名。"""

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MaterialError(f"材料要求文件不是 UTF-8：{path.name}") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = tuple(reader.fieldnames or ())
    missing = [name for name in COLUMNS if name not in fields]
    if missing:
        raise MaterialError(f"{path.name} 缺少必需列：{', '.join(missing)}")
    return [dict(row) for row in reader]


def parse(rows: Iterable[Mapping[str, str]], *, fact_keys: Sequence[str]) -> Catalog:
    """校验每一行；未仲裁的行跳过并留下原因，仲裁行的任何一处不合口径都直接报错。"""

    allowed = frozenset(fact_keys)
    importable: list[MaterialRow] = []
    skipped: list[tuple[str, str]] = []
    seen: dict[str, int] = {}

    for line_number, row in enumerate(rows, start=2):  # 2 = 表头之后的第一行
        material_id = _text(row, "material_id")
        where = f"第 {line_number} 行（{material_id or '缺少 material_id'}）"
        if not material_id:
            raise MaterialError(f"{where}：material_id 为空")
        if not _MATERIAL_ID.match(material_id):
            raise MaterialError(f"{where}：material_id 应形如 TAX-01 / GRANT-02")
        if material_id in seen:
            raise MaterialError(
                f"{where}：material_id 与第 {seen[material_id]} 行重复，同一材料只能出现在一行"
            )
        seen[material_id] = line_number

        review_status = _text(row, "review_status")
        if review_status != ARBITRATED:
            skipped.append((material_id, review_status or "（空）"))
            continue

        policy_type = _text(row, "policy_type")
        if policy_type not in _POLICY_TYPES:
            raise MaterialError(f"{where}：policy_type 只能是 tax 或 grant，当前 {policy_type!r}")

        title = _text(row, "title")
        if not title:
            raise MaterialError(f"{where}：title 为空")

        required = _text(row, "required")
        if required not in (REQUIRED_TRUE, REQUIRED_FALSE):
            raise MaterialError(f"{where}：required 只能是 必交 或 条件交，当前 {required!r}")
        is_required = required == REQUIRED_TRUE
        condition_desc = _text(row, "condition_desc") or None
        if is_required and condition_desc is not None:
            raise MaterialError(f"{where}：必交材料不写 condition_desc")
        if not is_required and condition_desc is None:
            raise MaterialError(f"{where}：条件交材料必须写 condition_desc，说明何时才需要")

        description = _text(row, "description")
        if not description:
            raise MaterialError(f"{where}：description 为空")

        keys = _split(_text(row, "proves_fact_keys"))
        unknown = [key for key in keys if key not in allowed]
        if unknown:
            raise MaterialError(
                f"{where}：proves_fact_keys 里有事实字典之外的键：{', '.join(unknown)}"
            )

        importable.append(
            MaterialRow(
                material_id=material_id,
                policy_type=policy_type,
                title=title,
                is_required=is_required,
                condition_desc=condition_desc,
                description=description,
                proves_fact_keys=keys,
                applies_to_policy_codes=_split(_text(row, "applies_to_policy_ids")),
                source_basis=_text(row, "source_basis"),
                review_status=review_status,
            )
        )

    return Catalog(importable=tuple(importable), skipped=tuple(skipped))


def read_policy_codes(path: Path) -> dict[str, tuple[str, ...]]:
    """``source_register.csv`` 的业务代号 → 该代号登记过的全部文号。

    库里没有「业务代号」列（`policy.version_id`/`policy_criterion.criterion_id` 都是导入时
    生成的 UUID），所以材料表里的代号只能在导入时借文号落到具体政策上。

    一个代号登记了多份文号是正常的：`CN-TAX-GXJS` 同时有已废止的 2008 版和现行 2016 版。
    这里照原样保留，是否歧义留到导入器判断——只有材料真正引用的代号才需要唯一，
    没被引用的代号不该卡住整份导入。
    """

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MaterialError(f"政策登记表不是 UTF-8：{path.name}") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = tuple(reader.fieldnames or ())
    missing = [name for name in ("policy_id", "version") if name not in fields]
    if missing:
        raise MaterialError(f"{path.name} 缺少必需列：{', '.join(missing)}")

    codes: dict[str, list[str]] = {}
    for row in reader:
        code = (row.get("policy_id") or "").strip()
        document_number = (row.get("version") or "").strip()
        if not code:
            continue
        if not document_number:
            raise MaterialError(f"{path.name} 的 {code} 没有文号，无法据此找到库里的政策版本")
        documents = codes.setdefault(code, [])
        if document_number not in documents:
            documents.append(document_number)
    return {code: tuple(documents) for code, documents in codes.items()}

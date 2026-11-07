"""评测输入快照的命令行入口（``docs/product/EVALUATION_FREEZE.md`` §5.2）。

    python -m app.evaluation inputs --out snapshot.json \
        --dataset-dir data/evaluation \
        --dataset-file labels.csv --dataset-file criteria_review.csv \
        --dataset-file enterprises.csv
    python -m app.evaluation verify snapshot.json --dataset-dir data/evaluation

``inputs`` 从库里导出政策/条件与企业/事实的映射，写入一份 JSON；``verify`` 拿那份
JSON 对当前库重算一遍，逐项列出差异，非零退出即「与签认时不一致，停止评测」。

清单里还有一份 ``datasetMapping``：把 ``--dataset-file`` 那几份 CSV 的行与库里的行
**逐条**对上（文号/标题 → 政策版本、表达式 → 条件、合成名称 → 企业、键+期间+来源+
值 → 事实），存对照表与未解决的问题。整文件哈希只证明文件没变，这份映射才证明
「文件就是导入进库的那一份」；有未解决项时 ``verify`` 一律非零退出。

清单里每份输入都连**路径**一起记（``dataset: [{"path": …, "sha256": …}]``）：项目内的
文件记相对项目根的 POSIX 路径（``data/evaluation/labels.csv``），项目外才记绝对路径。
三份评测输入本来就分散在 ``data/evaluation``、``data/policies``、``data/fixtures``，
只记文件名的话 ``verify`` 只能拿同名文件去顶替，换个目录就全错。同一个文件签认两次、
或两个不同目录的同名文件一起签，都直接拒绝（后者会让映射按文件名互相覆盖）。

数据集只认 ``--dataset-file`` 显式列出的文件（可重复），不按目录扫全部 CSV：
材料复核表、标注表也在同一个目录里，但它们不是评测输入，扫进来会让 ``verify``
在复核改动后误报。正式评测核对**要求三份齐全**（``labels.csv``、``criteria_review.csv``、
``enterprises.csv``），少一份直接非零退出——只对上两份证明不了整体对应。
真要只核对数据库，单独加 ``--no-dataset``：这样写出的清单 ``datasetMapping`` 为
``null``，``verify`` 也必须带同一个开关，成功时明说「仅数据库」，不能被当成数据集已签认。
``verify`` 核对的文件由签认清单**记下的路径**决定：``verify`` 不接受 ``--dataset-file``
（也不接受 ``--dataset-dir``），后传的文件不能顶替签认的对象。两侧 ``codeCommit`` 都是
``null`` 时它会提示「代码身份未证明」——没记不等于证明相同。

连接串只从环境变量 ``DATABASE_URL`` 读：命令行参数会留在 shell 历史与 ``ps`` 里，
错误信息也只说变量名，不打印值。部署机上用 ``systemd-run`` 带上服务同款环境变量：

    systemd-run --quiet --pipe --wait --collect \\
        -p EnvironmentFile=/etc/huice/api.env \\
        -p WorkingDirectory=/opt/huice/app/services/api \\
        /opt/huice/venv/bin/python -m app.evaluation inputs --out /tmp/eval-inputs.json

**代码身份不再靠手填**：清单顶层记 ``codeFingerprint``——对部署机上实际存在的
``services/api/app/**/*.py``、``contracts/openapi.yaml``、``services/api/requirements.lock``
算出的规范化哈希（实现见 :mod:`app.evaluation.code_fingerprint`）。任何人把某个提交
checkout 出来用同一条 ``python -m app.evaluation fingerprint`` 复算，就能把「提交 ↔
指纹」对上；``verify`` 在当前机器上重算并比对。``codeCommit`` 保留，但退化为**注记**。

指纹只回答「磁盘上这些文件是什么」，回答不了「进程里跑的是不是这份代码」——那一条
靠 ``--process-pid`` 做交叉核对：记下该进程的启动时间，并把「写于进程启动之后」的
代码文件全部列出来（盘上比进程新，说明可能 ``pull`` 了没重启），有这种文件就直接
停下。三件事（磁盘代码 / 对应哪个提交 / 进程里跑的是哪份）分开证明，不互相冒充。

**第 4 项「结果导出」的输入侧在 ``freeze-report``**：它把下载到的 API-13 报告裁成
不含「截至下载时刻的有效性」的冻结产物，剔除按固定标记整行/整段删除、可重复得到同一
份字节；原始报告与下载时间、两侧哈希由调用方另存审计。条件级结果仍只在报告正文里
（库里只落政策级结果），这是本轮记录在案的缺口。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import psycopg

from .code_fingerprint import (
    CODE_SCOPE,
    CodeFingerprintError,
    CodeInspection,
    default_code_root,
    inspect_code,
    process_started_at,
    stale_code_files,
)
from .dataset_map import REQUIRED_FILES, load_dataset_rows
from .report_freeze import ReportError, freeze_report
from .snapshot import SnapshotError, collect_input_snapshot, diff_snapshots

PROGRAM = "python -m app.evaluation"

#: 项目根（``services/api/app/evaluation/`` 往上四层）。清单里的相对路径一律以它为基准：
#: 记成「相对当前工作目录」的话，换个目录执行 ``verify`` 就会去翻别的地方。
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _signed_path(path: Path) -> str:
    """清单里记的路径：项目内用相对项目根的 POSIX 路径，项目外才用绝对路径。"""

    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve_signed_path(text: str) -> Path:
    """按签认清单里的路径取回文件：相对路径以项目根为基准，不受当前目录影响。"""

    path = Path(text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit(f"{PROGRAM}：缺少环境变量 DATABASE_URL（只列变量名，不输出值）")
    try:
        return psycopg.connect(dsn)
    except psycopg.Error:
        # psycopg 的报错里可能带 DSN；这里只说事实，不带原文
        raise SystemExit(f"{PROGRAM}：无法连接数据库（连接串不打印）") from None


def _dataset_entries(
    directory: str | None, files: Sequence[str]
) -> list[tuple[str, Path]]:
    """把 ``--dataset-file`` 变成（签认路径, 实际文件），并拒掉两种歧义。

    早先按目录扫 ``*.csv``，会把 ``material_review_sheet.csv``、``*_annotation_sheet.csv``
    这类非评测输入也算进签认清单——材料复核一改，``verify`` 就误报「数据集文件哈希不同」，
    而它们本来就不是要被冻结的输入（M3 复核提出）。现在只认 ``--dataset-file`` 列出的
    文件；``--dataset-dir`` 退化为解析相对路径的基准目录。

    两种歧义直接拒（M2 复审 P1）：
    * 同一个文件被列两次——第二次会悄悄覆盖第一次的哈希；
    * 两个**不同目录的同名文件**一起签——映射按文件名区分三份输入，后一个会顶替前一个。
    """

    base = Path(directory) if directory else Path.cwd()
    if directory is not None and not base.is_dir():
        raise SystemExit(f"{PROGRAM}：数据集目录不存在：{directory}")

    entries: list[tuple[str, Path]] = []
    seen_path: dict[str, str] = {}
    seen_name: dict[str, str] = {}
    for name in files:
        path = Path(name)
        if not path.is_absolute():
            path = base / path
        if not path.is_file():
            raise SystemExit(f"{PROGRAM}：数据集文件不存在：{name}")
        signed = _signed_path(path)
        if signed in seen_path:
            raise SystemExit(
                f"{PROGRAM}：同一个文件被签认了两次：{signed}（{seen_path[signed]} 与 {name}）"
            )
        seen_path[signed] = name
        if path.name in seen_name:
            raise SystemExit(
                f"{PROGRAM}：两个不同的文件同名 {path.name}：{seen_name[path.name]} 与 "
                f"{signed}——三份评测输入按文件名区分，同名会互相覆盖；请只签认其中一份"
            )
        seen_name[path.name] = signed
        entries.append((signed, path))
    return sorted(entries)


def _dataset_file_hash(entries: Sequence[tuple[str, Path]]) -> list[dict[str, str]]:
    """整文件哈希：与冻结声明 §3 的 sha256sum 口径一致，路径与哈希一起记。"""

    return [
        {"path": signed, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for signed, path in sorted(entries)
    ]


def _config_from_env(use_config: bool) -> dict[str, str] | None:
    return dict(os.environ) if use_config else None


def _require_required_files(names: Sequence[str]) -> None:
    """正式评测核对要三份输入齐全；缺哪份就点名哪份，不退回「只比数据库」。

    ``names`` 是签认路径，按**文件名**认三份输入（目录不同也算同一份）。
    """

    if not names:
        raise SystemExit(
            f"{PROGRAM}：正式评测核对要求三份评测输入（{'、'.join(REQUIRED_FILES)}），"
            "请用可重复的 --dataset-file 给出（只给 --dataset-dir 会按目录扫全部 CSV，"
            "把材料复核表这类非评测输入也算进去）；只核对数据库请显式加 --no-dataset"
        )
    provided = {PurePosixPath(name).name for name in names}
    missing = [name for name in REQUIRED_FILES if name not in provided]
    if missing:
        raise SystemExit(
            f"{PROGRAM}：缺少评测输入 {('、'.join(missing))}——三份都要给，"
            "少一份就只能证明部分对应关系"
        )


def _inspect_code() -> CodeInspection:
    """算部署文件指纹；范围内缺文件或一个都没扫到就停（不能把空集合算成指纹）。

    根目录取 :func:`default_code_root`（本工具所在的那棵树），**不取** ``PROJECT_ROOT``：
    后者决定签认路径的基准、测试里会被换成临时目录，跟着它算会让「换个工作目录」变成
    「代码变了」。
    """

    try:
        return inspect_code(default_code_root())
    except CodeFingerprintError as exc:
        raise SystemExit(f"{PROGRAM}：{exc}") from None


def _process_started(args: argparse.Namespace, inspection: CodeInspection) -> str | None:
    """记下服务进程启动时间，并核「有没有代码文件比进程还新」。

    有就停下：那说明盘上的代码比正在跑的进程新，进程里很可能是旧代码，此时签认的
    指纹虽然是对的，却对不上**实际在跑的东西**。这条只在本机（Linux）给 ``--process-pid``
    时才做——看不到进程，就不假装看过。
    """

    if args.process_pid is None:
        return None
    try:
        started = process_started_at(args.process_pid)
    except CodeFingerprintError as exc:
        raise SystemExit(f"{PROGRAM}：{exc}") from None
    stale = stale_code_files(inspection.files, started)
    if stale:
        listed = "、".join(f"{item.path}（写于 {item.mtime.isoformat()}）" for item in stale[:5])
        more = "" if len(stale) <= 5 else f"，另有 {len(stale) - 5} 个"
        raise SystemExit(
            f"{PROGRAM}：进程 {args.process_pid} 启动于 {started.isoformat()}，"
            f"但有 {len(stale)} 个代码文件写于其后：{listed}{more}——"
            "盘上是新的、进程里可能还是旧的，此时记下的指纹对不上正在跑的代码。"
            "请先重启服务再签认/核对"
        )
    return started.isoformat()


def _build_manifest(
    args: argparse.Namespace,
    *,
    signed_dataset: Sequence[tuple[str, Path]] | None = None,
) -> dict[str, Any]:
    collected_at = None
    if args.collected_at:
        try:
            collected_at = datetime.fromisoformat(args.collected_at)
        except ValueError:
            raise SystemExit(f"{PROGRAM}：--collected-at 需要 ISO 8601 时间") from None

    # 先把不依赖数据库的参数校验完再连库：参数错了不该先去碰生产库
    selected = args.dataset_file or []
    if args.no_dataset:
        # 显式声明「只核对数据库」：这种清单不能当作数据集映射通过，`verify` 会认模式
        if selected or args.dataset_dir is not None or signed_dataset:
            raise SystemExit(
                f"{PROGRAM}：--no-dataset 只能单独用——给了 --dataset-file/--dataset-dir "
                "就是数据集核对，不能同时声明只比数据库"
            )
        dataset = None
        dataset_rows = None
    elif signed_dataset is not None:
        # 核对哪几份文件由签认清单决定：命令行再点一次名就等于让后传的文件顶替签认对象
        if selected or args.dataset_dir is not None:
            raise SystemExit(
                f"{PROGRAM}：verify 核对哪些文件由签认清单决定（"
                f"{'、'.join(signed for signed, _ in signed_dataset) or '无'}），"
                "不要再传 --dataset-file/--dataset-dir：那会让后传的文件顶替签认的对象"
            )
        dataset = _dataset_file_hash(signed_dataset)
        _require_required_files([signed for signed, _ in signed_dataset])
        try:
            dataset_rows = load_dataset_rows([path for _, path in signed_dataset])
        except SnapshotError as exc:
            raise SystemExit(f"{PROGRAM}：{exc}") from None
    else:
        # 先报「目录不存在 / 点名的文件不存在」（路径写错要立刻看出来），再要求三份齐全
        entries = _dataset_entries(args.dataset_dir, selected)
        _require_required_files([signed for signed, _ in entries])
        dataset = _dataset_file_hash(entries)
        # 行内容与整文件哈希来自同一批文件：哈希证明「文件没变」，逐条映射证明「就是库里那份」
        try:
            dataset_rows = load_dataset_rows([path for _, path in entries])
        except SnapshotError as exc:
            raise SystemExit(f"{PROGRAM}：{exc}") from None
    # 指纹在连库之前算：既是参数校验的一部分，也避免算了半个清单才发现范围缺文件
    inspection = _inspect_code()
    process_started = _process_started(args, inspection)
    with _connect() as conn:
        return collect_input_snapshot(
            conn,
            config=_config_from_env(not args.no_config),
            dataset=dataset,
            dataset_rows=dataset_rows,
            code_commit=args.commit,
            code=inspection.manifest_fields(),
            process_started_at=process_started,
            collected_at=collected_at,
        )


def _dump(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _report_mapping(manifest: dict[str, Any]) -> None:
    """把 CSV ↔ 库对齐的结果说到 stderr；有问题不改退出码，但 `verify` 会拦住。"""

    mapping = manifest.get("datasetMapping")
    if not isinstance(mapping, dict):
        print(
            "注意：--no-dataset 模式，数据集与库的逐条映射没有做——"
            "这份清单只证明库没变，证明不了 CSV 就是库里那份，不能当作评测输入已签认",
            file=sys.stderr,
        )
        return
    print(
        "数据集映射：{} 个政策版本、{} 个企业、{} 条用例，未解决 {} 项".format(
            len(mapping.get("policies", [])),
            len(mapping.get("enterprises", [])),
            mapping.get("caseCount", 0),
            len(mapping.get("problems", [])),
        ),
        file=sys.stderr,
    )
    for problem in mapping.get("problems", []):
        print(f"  - {problem}", file=sys.stderr)


def _cmd_inputs(args: argparse.Namespace) -> int:
    manifest = _build_manifest(args)
    payload = _dump(manifest)
    if args.out == "-":
        sys.stdout.buffer.write(payload)
        _report_mapping(manifest)
        return 0
    Path(args.out).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    print(f"已写入 {args.out}（sha256 {digest}）", file=sys.stderr)
    _report_mapping(manifest)
    return 0


def _load_manifest(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SystemExit(f"{PROGRAM}：读不到清单 {path}：{exc.strerror}") from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(f"{PROGRAM}：{path} 不是 UTF-8 的 JSON") from None
    if not isinstance(manifest, dict) or "manifestVersion" not in manifest:
        raise SystemExit(f"{PROGRAM}：{path} 不是输入清单（缺少 manifestVersion）")
    return manifest


def _signed_dataset(expected: Mapping[str, Any]) -> list[tuple[str, Path]]:
    """从签认清单里取回数据集路径；按**签认路径**读文件，缺一份就停。

    只核对数据库的清单不记数据集（``dataset`` 为 ``null``），返回空表；其余情况必须是
    带路径的列表——旧格式（文件名 → 哈希）没法定位文件，直接要求重新签认。
    """

    entries = expected.get("dataset")
    if entries is None:
        return []
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise SystemExit(
            f"{PROGRAM}：这份清单的 dataset 不是「路径 + 哈希」格式"
            f"（清单版本 {expected.get('manifestVersion')!r}），无法按签认路径取文件——"
            "请用当前版本重新签认"
        )
    signed: list[tuple[str, Path]] = []
    for item in entries:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise SystemExit(f"{PROGRAM}：清单里的数据集条目缺少 path")
        text = item["path"]
        path = _resolve_signed_path(text)
        if not path.is_file():
            raise SystemExit(
                f"{PROGRAM}：签认的数据集文件不存在：{text}"
                f"（按签认路径读取；相对路径以项目根 {PROJECT_ROOT} 为基准）"
            )
        signed.append((text, path))
    return sorted(signed)


def _report_code_identity(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> None:
    """内容一致以后，把「代码身份到什么程度」说清楚——不能比实际更有把握。

    三种情形分开讲（M2 在 #39 的方向：``codeCommit=null`` 不因为磁盘指纹一致就笼统
    说成「运行代码身份已证明」）：

    * 清单里没有指纹（旧清单）：磁盘与进程都没证明；
    * 有指纹且一致：**磁盘代码**与签认一致；但「这份代码对应哪个提交」与「进程里跑的
      是不是它」仍然没证明，各自需要 checkout 复算与重启后的启动时间核对；
    * 有指纹但签认时没记启动时间：上面第二句仍成立，只是少了一条交叉证据。
    """

    fingerprint = expected.get("codeFingerprint")
    if fingerprint is None:
        print(
            "提示：这份清单没有 codeFingerprint（签认于引入部署文件指纹之前），磁盘代码身份"
            "未证明——要用当前版本重新签认，才会记下可复算的部署文件指纹",
            file=sys.stderr,
        )
        return

    short = str(fingerprint)[:12]
    count = expected.get("codeFiles")
    print(
        f"提示：磁盘代码与签认一致（指纹 {short}…，{count} 个文件）；"
        "但「这份代码对应哪个提交」与「进程里跑的是不是它」仍未证明——"
        "前者要把该提交 checkout 出来用 fingerprint 子命令复算，"
        "后者要在签认时用 --process-pid 记下进程启动时间做交叉核对",
        file=sys.stderr,
    )
    if expected.get("processStartedAt") is None:
        print(
            "提示：签认时没有记录服务进程启动时间（--process-pid），"
            "因此「盘上的代码」与「进程里的代码」之间没有交叉证据",
            file=sys.stderr,
        )


def _cmd_verify(args: argparse.Namespace) -> int:
    expected = _load_manifest(args.manifest)
    # 模式必须一致：只比数据库的清单不能说成「数据集核对通过」，反之也不行
    db_only = expected.get("datasetMapping") is None
    if args.no_dataset != db_only:
        if args.no_dataset:
            raise SystemExit(
                f"{PROGRAM}：这份清单签认时核对了评测输入 CSV（datasetMapping 非空），"
                "不能用 --no-dataset 核对"
            )
        raise SystemExit(
            f"{PROGRAM}：这份清单签认时只核对了数据库（datasetMapping 为 null），"
            "确认它请显式加 --no-dataset；正式评测冻结要的是带数据集映射的清单"
        )
    # 核对哪些文件由**签认清单记下的路径**决定：签认时列了谁就查谁，不跟着当前目录里的
    # 同名文件走，也不接受命令行重新点名（M2 复审 P1）
    signed = _signed_dataset(expected)
    actual = _build_manifest(args, signed_dataset=signed)
    problems = diff_snapshots(expected, actual)
    if problems:
        print("与签认清单不一致：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    _report_code_identity(expected, actual)
    if args.no_dataset:
        print(
            "一致（仅数据库）：{} 个政策版本、{} 个企业；未核对评测输入 CSV，"
            "此结果不能当作数据集已签认".format(
                len(actual.get("policies", [])), len(actual.get("enterprises", []))
            )
        )
        return 0
    mapping = actual.get("datasetMapping") or {}
    print(
        "一致：{} 个政策版本、{} 个企业；数据集映射 {} 个政策版本、{} 个企业、{} 条用例".format(
            len(actual.get("policies", [])),
            len(actual.get("enterprises", [])),
            len(mapping.get("policies", [])),
            len(mapping.get("enterprises", [])),
            mapping.get("caseCount", 0),
        )
    )
    return 0


def _cmd_freeze_report(args: argparse.Namespace) -> int:
    path = Path(args.report)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"{PROGRAM}：读不到报告 {args.report}：{exc.strerror}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"{PROGRAM}：{args.report} 不是 UTF-8 文本") from None
    try:
        frozen = freeze_report(text)
    except ReportError as exc:
        raise SystemExit(f"{PROGRAM}：{exc}") from None

    payload = frozen.text.encode("utf-8")
    if args.out == "-":
        sys.stdout.buffer.write(payload)
    else:
        Path(args.out).write_bytes(payload)
    # 审计信息走 stderr：--out - 时 stdout 必须是干净的冻结正文
    print(
        json.dumps(
            {
                "source": args.report,
                "sourceSha256": hashlib.sha256(raw).hexdigest(),
                "frozenSha256": hashlib.sha256(payload).hexdigest(),
                "downloadedAt": frozen.downloaded_at,
                "droppedLines": frozen.dropped_lines,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 0


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    """只算指纹、不连库：任何一台机器上都能跑，用来把「提交 ↔ 指纹」对上。"""

    root = Path(args.root).resolve() if args.root else default_code_root()
    try:
        inspection = inspect_code(root)
    except CodeFingerprintError as exc:
        raise SystemExit(f"{PROGRAM}：{exc}") from None

    if args.json:
        payload = {
            "fingerprint": inspection.fingerprint,
            "fileCount": len(inspection.files),
            "scope": list(CODE_SCOPE),
            # 逐文件明细：两棵树差在哪，比对这份就能看出来，不必先相信顶层哈希
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size}
                for item in inspection.files
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    # 默认只在 stdout 打指纹本身：要贴进报告、要写进脚本的都是这一行
    print(inspection.fingerprint)
    print(
        f"（{len(inspection.files)} 个文件，范围 {', '.join(CODE_SCOPE)}；加 --json 看逐文件哈希）",
        file=sys.stderr,
    )
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset-file",
        action="append",
        metavar="NAME",
        help=(
            "签认的评测输入 CSV，可重复（如 --dataset-file data/evaluation/labels.csv）；"
            "只哈希列出的文件，相对路径按 --dataset-dir 解析。verify 不接受它："
            "核对哪些文件由签认清单里的路径决定"
        ),
    )
    parser.add_argument(
        "--dataset-dir",
        help=(
            "解析 --dataset-file 相对路径的基准目录（如 data/evaluation）；不按目录扫 CSV。"
            "verify 不接受它"
        ),
    )
    parser.add_argument(
        "--no-dataset",
        action="store_true",
        help=(
            "只核对数据库、不核对评测输入 CSV（清单的 datasetMapping 为 null，verify 也要带这个开关）；"
            "正式评测冻结必须给三份 CSV，不能用它替代"
        ),
    )
    parser.add_argument(
        "--commit",
        help="代码提交号；云端没有 .git，需要显式给出，缺省记 null",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="不把当前进程的非敏感配置计入哈希（默认计入）",
    )
    parser.add_argument(
        "--collected-at",
        help="覆盖清单的采集时间（ISO 8601），便于复现同一份清单",
    )
    parser.add_argument(
        "--process-pid",
        type=int,
        help=(
            "正在运行的服务进程 PID（如 systemctl show -p MainPID huice-api 取到的值）："
            "记下它的启动时间，并把「写于进程启动之后」的代码文件一并列出——"
            "有这种文件就停下（盘上新的、进程里可能还是旧的）。只在 Linux 上可用"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="评测输入快照的导出与核对（EVALUATION_FREEZE §5.2）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inputs = sub.add_parser("inputs", help="从数据库导出输入清单")
    inputs.add_argument("--out", default="-", help="输出文件，默认写到标准输出")
    _common(inputs)
    inputs.set_defaults(handler=_cmd_inputs)

    verify = sub.add_parser("verify", help="拿已签认的清单核对当前库")
    verify.add_argument("manifest", help="已签认的清单 JSON")
    _common(verify)
    verify.set_defaults(handler=_cmd_verify)

    fingerprint = sub.add_parser(
        "fingerprint",
        help="打印部署文件指纹（不连库）：用来把「提交 ↔ 指纹」对上",
    )
    fingerprint.add_argument(
        "--root",
        help="要算指纹的代码根目录，默认是本工具所在仓库的根（换一棵树复算时用）",
    )
    fingerprint.add_argument(
        "--json", action="store_true", help="输出指纹与逐文件哈希（JSON）"
    )
    fingerprint.set_defaults(handler=_cmd_fingerprint)

    freeze = sub.add_parser(
        "freeze-report",
        help="把下载的 API-13 报告裁成不含有效性段的冻结产物（§5.2 第 4 项）",
    )
    freeze.add_argument("report", help="从 API-13 下载得到的 report.md")
    freeze.add_argument("--out", default="-", help="冻结产物的输出文件，默认写到标准输出")
    freeze.set_defaults(handler=_cmd_freeze_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

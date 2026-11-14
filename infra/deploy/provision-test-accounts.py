#!/usr/bin/env python3
"""在隔离环境里建联调要用的合成账号：1 个审核员 + 2 个企业（#59、PR #72）。

M2 的前端联调要「同版隔离环境 + 两个合成企业账号 + 一个审核员账号」。审核员可以用
``create-admin.py`` 建，但那个脚本只建审核员、不建企业，也不生成可交付的口令——两个
企业账号还是得有人手工注册，注册出来的口令只有注册人知道。这个脚本把那一步固化成
一次可复跑的动作。

四条护栏（M2 在 PR #74 两轮复审里点名）：

1. **先验身份再动手**：同名账号先核对角色、状态与企业归属；任何一项不符就整批停下
   （退出码 5），不建、不改、不重置——``--reset-passwords`` 会顺带撤销会话。
2. **先落盘、后提交**（写前日志）：先把新口令写进凭证文件并 fsync，再调
   ``register()`` / ``update_password()``，成功后把该行的状态从「待提交」改成
   「新建」/「口令已重置」。进程在两步之间被杀、或提交失败，口令都已经在文件里。
   重置时**旧口令与新口令同时保留**（旧的在 ``password=``、新的在 ``pending=``）：
   提交没成功时旧口令仍是唯一有效的，不能因为一次失败的重置把它丢掉。下次运行
   按库里的哈希决定哪一份有效——新口令生效就提成正式口令，否则丢弃 pending、
   保留旧口令；两份都对不上才把该行作废。
3. **合并而不是覆盖**：凭证文件按用户名合并——已记录的口令原样保留，只追加/更新本轮
   变动的账号。部分账号已存在、部分新建时，旧口令不会被一份只剩新建账号的文件盖掉。
4. **文件按受控方式创建**：父目录不允许组/其他可写；目标不能是符号链接；新文件
   ``O_EXCL`` + ``0600``；更新时写同目录临时文件再 ``os.replace``，不留宽权限窗口。

并发：``--out`` 指向同一个文件跑两份脚本没有意义；两个进程同时建同名账号时，后到的
会在 ``register()`` 上撞 ``USERNAME_TAKEN``——脚本会重新核对身份，确认是合成账号后
**中止并退出 6**（它的口令在另一个进程手里，本文件拿不到），而不是装作成功。

用法（ECS，root；``DATABASE_URL`` 从 ``api.env`` 读，不上命令行）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
        -p WorkingDirectory=/opt/huice/app \\
        /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/provision-test-accounts.py \\
        --out /root/huice-test-accounts-$(date +%Y%m%d).txt

凭证文件只走机密渠道交付（当面对传、私聊、受控网盘），**不进仓库、不进文档、不进聊天、
不进 PR**。这些账号是联调账号，不是验收证据。

退出码：0 成功（含「账号已存在、本轮无改动」）；2 环境变量或参数不合法（只报变量名）；
3 写库失败；4 凭证文件路径不可用；5 同名账号身份不符（整批未执行）；6 并发冲突
（账号被另一个进程创建，本进程拿不到口令）。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

try:  # noqa: E402
    from app import auth as auth_tools
    from app.agents.profile import normalize_profile
    from app.db import Database
    from app.errors import ApiProblem
    from app.integrations.accounts import AccountsRepository
    from app.schemas import EnterpriseInput
except ModuleNotFoundError as exc:  # noqa: E402
    print(
        f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行本脚本"
        "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

#: 联调用的画像年度：与仓库里的合成数据同一年，避免两年混用看不出期间错位。
PROFILE_YEAR = ("2026-01-01", "2026-12-31")

HEADER = (
    "# 惠策协同 · 隔离环境合成账号（仅供 E1/E2 同版联调，不是验收证据）",
    "# 交付走机密渠道；不要入库、不要贴进 PR/文档/聊天。用完按 infra/deploy/README.md §3.6 清理。",
)

#: 一行一条凭证。状态放在末尾括号里，便于「先落盘、后提交」时区分待提交与已完成。
LINE_PATTERN = re.compile(
    r"^role=(?P<role>\S+) username=(?P<username>\S+) password=(?P<password>\S+)"
    r"(?: pending=(?P<pending>\S+))?"
    r"(?: enterprise=(?P<enterprise>.+?))? \((?P<status>[^()]+)\)$"
)

STATUS_PENDING = "待提交"


class ConcurrentConflict(RuntimeError):
    """另一个进程刚建了同名账号：本进程拿不到它的口令，只能停下。"""


@dataclass(frozen=True)
class AccountSpec:
    """一个合成账号。企业账号必须给企业名与两项必需事实（region、industry）。"""

    username: str
    role: str
    name_synthetic: str | None = None
    region: str | None = None
    industry: str | None = None

    @property
    def is_enterprise(self) -> bool:
        return self.role == "enterprise"


#: 两个企业用同一地区、不同行业：越权用例要看的是「看不见对方」，不是地区差异。
SPECS = (
    AccountSpec("reviewer.demo", "reviewer"),
    AccountSpec("acme.alpha", "enterprise", "合成企业A", "440300", "F51"),
    AccountSpec("acme.beta", "enterprise", "合成企业B", "440300", "C39"),
)


@dataclass
class CredentialEntry:
    username: str
    role: str
    password: str
    enterprise: str | None
    status: str
    #: 重置进行中的新口令。重置提交结果未知时，旧口令与它同时留在文件里，
    #: 下次运行按库里的哈希决定哪一份有效——不能因为重置失败丢掉唯一有效的旧口令。
    pending: str | None = None


def _enterprise_input(spec: AccountSpec) -> EnterpriseInput:
    period = {"startDate": PROFILE_YEAR[0], "endDate": PROFILE_YEAR[1]}
    return EnterpriseInput.model_validate(
        {
            "nameSynthetic": spec.name_synthetic,
            "period": period,
            "facts": [
                {
                    "key": "region",
                    "value": spec.region,
                    "unit": "code",
                    "period": period,
                    "source": "user_input",
                },
                {
                    "key": "industry",
                    "value": spec.industry,
                    "unit": "code",
                    "period": period,
                    "source": "user_input",
                },
            ],
        }
    )


def _generate_password() -> str:
    """24 位随机口令：长度远在 12—128 之内，不含空白，可直接粘进登录框。"""

    return secrets.token_urlsafe(18)


def _read_dsn() -> str | None:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("缺少环境变量 DATABASE_URL（本脚本只从环境读，不打印取值）。", file=sys.stderr)
        return None
    if not dsn.startswith(("postgresql://", "postgres://")):
        print("DATABASE_URL 不是可用的 PostgreSQL 连接串。", file=sys.stderr)
        return None
    return dsn


# --------------------------------------------------------------- 凭证文件


def _check_out_path(path: Path, *, force: bool) -> bool:
    """动手前先确认凭证文件写得下去、且不会被别人读到。"""

    if path.is_symlink():
        print(f"凭证文件 {path} 是符号链接：不接受，请换成普通文件路径。", file=sys.stderr)
        return False
    if path.exists() and not force:
        print(
            f"凭证文件 {path} 已存在：加 --force 继续（已有的口令会保留，只追加/更新本轮变动的账号），"
            "或换一个 --out 路径。",
            file=sys.stderr,
        )
        return False
    parent = path.parent
    if not parent.is_dir():
        print(f"凭证文件所在目录不存在：{parent}", file=sys.stderr)
        return False
    if os.name == "posix":
        if parent.stat().st_mode & 0o022:
            print(
                f"凭证文件所在目录 {parent} 允许组或其他用户写入：换到只有你能写的目录"
                "（如 /root），否则别人可以在里面放符号链接或替换文件。",
                file=sys.stderr,
            )
            return False
    return True


def _credential_line(entry: CredentialEntry) -> str:
    parts = [f"role={entry.role}", f"username={entry.username}", f"password={entry.password}"]
    if entry.pending:
        parts.append(f"pending={entry.pending}")
    if entry.enterprise:
        parts.append(f"enterprise={entry.enterprise}")
    parts.append(f"({entry.status})")
    return " ".join(parts)


def load_credentials(path: Path) -> tuple[dict[str, CredentialEntry], list[str]]:
    """读回已有凭证：``username → 条目``，无法识别的行原样保留。"""

    entries: dict[str, CredentialEntry] = {}
    extras: list[str] = []
    if not path.exists():
        return entries, extras
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        match = LINE_PATTERN.match(raw.strip())
        if match is None:
            extras.append(raw)
            continue
        entries[match["username"]] = CredentialEntry(
            username=match["username"],
            role=match["role"],
            password=match["password"],
            enterprise=match["enterprise"],
            status=match["status"],
            pending=match["pending"],
        )
    return entries, extras


def render_credentials(entries: dict[str, CredentialEntry], extras: list[str]) -> str:
    """按固定顺序渲染：先本轮三个账号，再其它已记录的账号，最后是无法识别的行。"""

    lines = list(HEADER)
    known = {spec.username for spec in SPECS}
    for spec in SPECS:
        entry = entries.get(spec.username)
        if entry is not None:
            lines.append(_credential_line(entry))
    for username, entry in entries.items():
        if username not in known:
            lines.append(_credential_line(entry))
    lines.extend(extras)
    return "\n".join(lines) + "\n"


def persist_credentials(
    path: Path,
    entries: dict[str, CredentialEntry],
    extras: list[str],
    *,
    create_only: bool,
) -> None:
    """原子写入：新文件 ``O_EXCL`` + ``0600``，更新写临时文件再 ``os.replace``。"""

    data = render_credentials(entries, extras).encode("utf-8")
    if create_only:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return

    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:  # pragma: no cover - 清理失败不掩盖原始异常
            pass
        raise


def _reconcile(
    accounts: AccountsRepository, entries: dict[str, CredentialEntry]
) -> tuple[list[str], bool]:
    """按库里的口令哈希核对每一行：账号不在、或口令已不是当前口令的行作废。

    返回 ``(作废的用户名, 是否有改动)``——有改动时调用方要把结果写回文件，否则文件里
    会一直留着失效口令或「待提交」状态。
    """

    dropped: list[str] = []
    changed = False
    for username in list(entries):
        entry = entries[username]
        row = accounts.find_user_by_username(username)
        if row is None:
            del entries[username]
            dropped.append(username)
            changed = True
            continue
        stored = row["password_hash"]
        if entry.pending is not None:
            # 重置进行中：库里是新的就是提交成功了，是旧的就是没提交，两份都留着
            if auth_tools.verify_password(entry.pending, stored):
                entry.password = entry.pending
                entry.pending = None
                entry.status = "口令已重置"
                changed = True
            elif auth_tools.verify_password(entry.password, stored):
                entry.pending = None
                entry.status = "已确认"
                changed = True
            else:
                del entries[username]
                dropped.append(username)
                changed = True
            continue
        if not auth_tools.verify_password(entry.password, stored):
            del entries[username]
            dropped.append(username)
            changed = True
            continue
        if entry.status != "已确认":
            entry.status = "已确认"
            changed = True
    return dropped, changed


# --------------------------------------------------------------- 身份核对


@dataclass(frozen=True)
class PlanItem:
    spec: AccountSpec
    action: str  # create / reset / skip
    user_id: object | None = None


def _identity_problem(accounts: AccountsRepository, spec: AccountSpec) -> str | None:
    """同名账号必须就是我们要的那个合成账号，否则返回原因。"""

    row = accounts.find_user_by_username(spec.username)
    if row is None:
        return None
    if row["role"] != spec.role:
        return f"角色是 {row['role']!r}，预期 {spec.role!r}"
    if row["status"] != "active":
        return f"账号状态是 {row['status']!r}，不是 active"
    enterprise = accounts.enterprise_name_for_user(row["id"])
    if spec.is_enterprise:
        if enterprise is None:
            return "账号没有绑定企业"
        if enterprise != spec.name_synthetic:
            return f"企业名是 {enterprise!r}，预期 {spec.name_synthetic!r}"
    elif enterprise is not None:
        return f"审核员账号却绑定了企业 {enterprise!r}"
    return None


def build_plan(
    accounts: AccountsRepository,
    entries: dict[str, CredentialEntry],
    *,
    reset_passwords: bool,
) -> tuple[list[PlanItem], list[str], list[str]]:
    """返回 (计划, 身份问题, 提示)。

    已存在且本文件有有效口令的账号算 ``skip``；文件里没有它的口令才需要 ``--reset-passwords``。
    """

    items: list[PlanItem] = []
    problems: list[str] = []
    warnings: list[str] = []
    for spec in SPECS:
        row = accounts.find_user_by_username(spec.username)
        if row is None:
            items.append(PlanItem(spec, "create"))
            continue
        problem = _identity_problem(accounts, spec)
        if problem is not None:
            problems.append(f"{spec.username}：{problem}")
            continue
        # 显式重置优先：文件里有旧口令也一样重置（会撤销该账号的现有会话）
        if reset_passwords:
            items.append(PlanItem(spec, "reset", row["id"]))
            continue
        if spec.username in entries:
            items.append(PlanItem(spec, "skip", row["id"]))
            continue
        warnings.append(f"{spec.username}：账号已存在，但本文件没有它的口令")
        items.append(PlanItem(spec, "skip", row["id"]))
    return items, problems, warnings


def _provision(
    accounts: AccountsRepository,
    items: list[PlanItem],
    entries: dict[str, CredentialEntry],
    extras: list[str],
    path: Path,
    *,
    create_only: bool,
) -> tuple[int, int]:
    """按计划建号：**先落盘、后提交**，成功后把状态从「待提交」改成最终状态。"""

    created = 0
    skipped = 0
    # 只有第一次写入走 O_EXCL（「不覆盖别人刚建的文件」），之后都是更新
    first_write = create_only
    for item in items:
        spec = item.spec
        if item.action == "skip":
            skipped += 1
            continue

        existing = entries.get(spec.username)
        if item.action == "reset" and existing is not None:
            # 重置：旧口令留着，新口令放 pending——提交没成功时旧口令仍是唯一有效的
            existing.pending = _generate_password()
            existing.status = "口令重置中"
            entry = existing
        else:
            entry = CredentialEntry(
                username=spec.username,
                role=spec.role,
                password=_generate_password(),
                enterprise=spec.name_synthetic,
                status=STATUS_PENDING,
            )
            entries[spec.username] = entry
        # 写前日志：口令先落地，提交失败或进程被杀时它还在文件里
        persist_credentials(path, entries, extras, create_only=first_write)
        first_write = False

        try:
            if item.action == "reset":
                new_password = entry.pending or entry.password
                accounts.update_password(
                    user_id=item.user_id, password_hash=auth_tools.hash_password(new_password)
                )
                entry.password = new_password
                entry.pending = None
                entry.status = "口令已重置"
            else:
                profile = (
                    normalize_profile(_enterprise_input(spec)) if spec.is_enterprise else None
                )
                accounts.register(
                    username=spec.username,
                    password_hash=auth_tools.hash_password(entry.password),
                    role=spec.role,
                    profile=profile,
                )
                entry.status = "新建"
        except ApiProblem as exc:
            if exc.code == "USERNAME_TAKEN":
                # 另一个进程刚刚建了同名账号：重新核对身份，然后中止——
                # 它的口令在对方手里，本进程装作成功只会让人拿着一份作废口令。
                entries.pop(spec.username, None)
                persist_credentials(path, entries, extras, create_only=False)
                problem = _identity_problem(accounts, spec)
                raise ConcurrentConflict(
                    f"{spec.username} 被另一个进程创建"
                    + (f"（{problem}）" if problem else "")
                ) from exc
            if item.action == "create":
                entries.pop(spec.username, None)
            # 重置失败时保留旧口令（可能还有 pending），不能连旧口令一起删掉
            persist_credentials(path, entries, extras, create_only=False)
            raise
        except BaseException:
            # 提交结果未知（连接断了、进程被杀）：旧口令与 pending 都留在文件里，
            # 下次运行按库里的哈希决定哪一份有效——绝不丢掉唯一有效的旧口令
            persist_credentials(path, entries, extras, create_only=False)
            raise

        persist_credentials(path, entries, extras, create_only=False)
        created += 1
    return created, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建隔离环境的合成账号（1 审核员 + 2 企业）")
    parser.add_argument("--out", type=Path, required=True, help="凭证文件路径（写成 0600）")
    parser.add_argument(
        "--force", action="store_true", help="凭证文件已存在时继续（已有口令保留并合并）"
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="已存在的账号也重新生成口令（默认跳过，避免踢掉正在用的会话）",
    )
    args = parser.parse_args(argv)

    dsn = _read_dsn()
    if dsn is None:
        return 2
    if not _check_out_path(args.out, force=args.force):
        return 4

    entries, extras = load_credentials(args.out)
    existed = args.out.exists()

    # 连不上库时连接池会在 close() 时再喊一句「couldn't stop thread」——那句话不说原因。
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

    database = Database(dsn, max_size=1)
    try:
        accounts = AccountsRepository(database)
        dropped, reconciled = _reconcile(accounts, entries)
        for username in dropped:
            print(f"{username}：凭证文件里的口令与库里对不上（账号没建出来或已被改过），该行已作废。")
        if reconciled and existed and not args.reset_passwords:
            persist_credentials(args.out, entries, extras, create_only=False)
            print(f"对账结果已写回 {args.out}（作废行删除、匹配行标为已确认）。")

        items, problems, warnings = build_plan(
            accounts, entries, reset_passwords=args.reset_passwords
        )
        if problems:
            print(
                "同名账号与合成账号不符，整批未执行（没建、没改、没重置）：\n  "
                + "\n  ".join(problems)
                + "\n请先确认这是不是同一台环境；要接管这些用户名，先人工改掉库里同名账号。",
                file=sys.stderr,
            )
            return 5

        pending = [item for item in items if item.action != "skip"]
        if not pending:
            for warning in warnings:
                print(f"注意：{warning}，本轮拿不到它的口令。要重发加 --reset-passwords。")
            print(
                f"{len(items)} 个合成账号都已存在，本轮没有新口令：{args.out} 未改动。"
                if existed
                else f"{len(items)} 个合成账号都已存在，本轮没有新口令，未生成凭证文件。"
            )
            return 0

        try:
            created, skipped = _provision(
                accounts, items, entries, extras, args.out, create_only=not existed
            )
        except ConcurrentConflict as exc:
            print(
                f"并发冲突：{exc}。本次未把它写进凭证文件——请确认另一个进程，"
                "或加 --reset-passwords 重发口令。",
                file=sys.stderr,
            )
            return 6
        except Exception as exc:  # 不打印异常详情：psycopg 的报错里带主机与库名
            print(
                f"建账号失败（{type(exc).__name__}）：已生成的口令保留在 {args.out}；"
                "请确认迁移 009 已执行、DATABASE_URL 指向本环境的库、账号有写权限。",
                file=sys.stderr,
            )
            return 3
    finally:
        database.close()

    for warning in warnings:
        print(f"注意：{warning}，本轮拿不到它的口令。要重发加 --reset-passwords。")
    print(
        f"已在 {len(SPECS)} 个合成账号上处理完成：新建/重置 {created} 个，"
        f"已存在跳过 {skipped} 个。口令只写进 {args.out}（0600），未回显。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

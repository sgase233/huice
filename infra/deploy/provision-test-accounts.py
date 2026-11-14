#!/usr/bin/env python3
"""在隔离环境里建联调要用的合成账号：1 个审核员 + 2 个企业（#59、PR #72）。

M2 的前端联调要「同版隔离环境 + 两个合成企业账号 + 一个审核员账号」。审核员可以用
``create-admin.py`` 建，但那个脚本只建审核员、不建企业，也不生成可交付的口令——两个
企业账号还是得有人手工注册，注册出来的口令只有注册人知道。这个脚本把那一步固化成
一次可复跑的动作：

- 账号是**合成**的：用户名固定（``reviewer.demo`` / ``acme.alpha`` / ``acme.beta``），
  企业名写明「合成企业」；
- 口令由脚本生成（24 位随机），**只写进 0600 的凭证文件**，不进 stdout、不进命令行；
- 已有账号默认**跳过**（不改口令，避免把 M2 正在用的账号踢掉），``--reset-passwords``
  才重新生成；
- 走的是注册同一条仓储路径（``AccountsRepository.register`` + ``normalize_profile``），
  企业归属、事实校验、用户名规则与 ``POST /auth/register`` 一致，不另写一套 SQL。

四条护栏（M2 在 PR #74 复审里点名）：

1. **先验身份再动手**：同名账号必须先核对角色、状态与企业归属；任何一项不符就整批停下
   （退出码 5），不建、不改、不重置——``--reset-passwords`` 会顺带撤销该账号的会话，
   误伤真实账号的代价比少建一个合成账号大得多。
2. **口令边建边落盘**：每建好（或重置）一个账号就写一行并 fsync，中途任何一步失败，
   已经产生的口令都在文件里，不会出现「账号建了、口令没了」。
3. **文件按受控方式创建**：父目录不允许组/其他可写；目标不能是符号链接；新文件用
   ``O_EXCL`` + ``0600`` 创建；覆盖时先写同目录临时文件再 ``os.replace`` 原子替换，
   宽权限的旧文件不会出现「写内容时还是 0644」的窗口。
4. **没有新口令就不动文件**：三个账号都已存在且没给 ``--reset-passwords`` 时，不重写
   凭证文件——否则唯一一份还在用的口令会被一个只剩标题的文件覆盖掉。

用法（ECS，root；``DATABASE_URL`` 从 ``api.env`` 读，不上命令行）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
        -p WorkingDirectory=/opt/huice/app \\
        /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/provision-test-accounts.py \\
        --out /root/huice-test-accounts-$(date +%Y%m%d).txt

本地（仓库根目录）::

    DATABASE_URL=... services/api/.venv/Scripts/python.exe \\
        infra/deploy/provision-test-accounts.py --out ./.local-test-accounts.txt

凭证文件只走机密渠道交付（当面对传、私聊、受控网盘），**不进仓库、不进文档、不进聊天、
不进 PR**；交付后按 §3.6 的做法登录验证，用完即删。这些账号是联调账号，不是验收证据。

退出码：0 成功（含「账号已存在、本次跳过」「没有新口令、文件未改动」）；2 环境变量或参数
不合法（只报变量名）；3 写库失败；4 凭证文件路径不可用（已存在且没给 ``--force``、父目录
不可写、目标是符号链接）；5 同名账号身份不符（角色／状态／企业归属），整批未执行。
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

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
    """动手前先确认凭证文件写得下去、且不会被别人读到。

    反过来做会留下「账号建好了、口令没落盘」的账号——口令只在这里出现一次。
    """

    if path.is_symlink():
        print(f"凭证文件 {path} 是符号链接：不接受，请换成普通文件路径。", file=sys.stderr)
        return False
    if path.exists() and not force:
        print(
            f"凭证文件 {path} 已存在：加 --force 覆盖，或换一个 --out 路径。"
            "覆盖会重写本次生成的口令，请确认旧文件没有被谁拿着用。",
            file=sys.stderr,
        )
        return False
    parent = path.parent
    if not parent.is_dir():
        print(f"凭证文件所在目录不存在：{parent}", file=sys.stderr)
        return False
    if os.name == "posix":
        mode = parent.stat().st_mode
        if mode & 0o022:
            print(
                f"凭证文件所在目录 {parent} 允许组或其他用户写入：换到只有你能写的目录"
                "（如 /root），否则别人可以在里面放符号链接或替换文件。",
                file=sys.stderr,
            )
            return False
    return True


class CredentialFile:
    """边建边写：每产生一条口令就落盘并 fsync。

    ``force`` 时先写同目录临时文件（``0600``、``O_EXCL``），收尾再 ``os.replace`` 原子
    替换；中途失败时临时文件保留，已生成的口令不会丢。非 ``force`` 时直接用 ``O_EXCL``
    建目标文件——「不覆盖」由内核保证，不靠先检查后打开。
    """

    def __init__(self, path: Path, *, force: bool) -> None:
        self.path = path
        self.tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        self._force = force
        self._handle: TextIO | None = None
        self.lines_written = 0

    def __enter__(self) -> "CredentialFile":
        target = self.tmp_path if self._force else self.path
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(target, flags, 0o600)
        self._handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        self._handle.write("\n".join(HEADER) + "\n")
        self._handle.flush()
        return self

    def write(self, line: str) -> None:
        assert self._handle is not None
        self._handle.write(line + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self.lines_written += 1

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self._handle is not None
        self._handle.close()
        if exc_type is not None:
            # 失败路径：临时文件原样保留，调用方会把路径告诉人
            return False
        if self._force:
            os.replace(self.tmp_path, self.path)
        os.chmod(self.path, 0o600)
        return False


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


def _plan(accounts: AccountsRepository, *, reset_passwords: bool) -> tuple[list[PlanItem], list[str]]:
    items: list[PlanItem] = []
    problems: list[str] = []
    for spec in SPECS:
        row = accounts.find_user_by_username(spec.username)
        if row is None:
            items.append(PlanItem(spec, "create"))
            continue
        problem = _identity_problem(accounts, spec)
        if problem is not None:
            problems.append(f"{spec.username}：{problem}")
            continue
        items.append(PlanItem(spec, "reset" if reset_passwords else "skip", row["id"]))
    return items, problems


def _credential_line(spec: AccountSpec, password: str, *, note: str) -> str:
    parts = [f"role={spec.role}", f"username={spec.username}", f"password={password}"]
    if spec.is_enterprise:
        parts.append(f"enterprise={spec.name_synthetic}")
    parts.append(f"({note})")
    return " ".join(parts)


def _provision(
    accounts: AccountsRepository, items: list[PlanItem], out: CredentialFile | None
) -> tuple[int, int]:
    """按计划建号；返回 (新产生口令的账号数, 跳过数)。口令边生成边交给 out。"""

    created = 0
    skipped = 0
    for item in items:
        spec = item.spec
        if item.action == "skip":
            skipped += 1
            continue

        password = _generate_password()
        if item.action == "reset":
            accounts.update_password(
                user_id=item.user_id, password_hash=auth_tools.hash_password(password)
            )
            if out is not None:
                out.write(_credential_line(spec, password, note="口令已重置"))
            created += 1
            continue

        profile = normalize_profile(_enterprise_input(spec)) if spec.is_enterprise else None
        try:
            accounts.register(
                username=spec.username,
                password_hash=auth_tools.hash_password(password),
                role=spec.role,
                profile=profile,
            )
        except ApiProblem as exc:
            # 并发跑两份脚本时后到的会撞用户名：当成「已存在」处理，不覆盖口令。
            if exc.code != "USERNAME_TAKEN":
                raise
            skipped += 1
            continue
        if out is not None:
            out.write(_credential_line(spec, password, note="新建"))
        created += 1
    return created, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建隔离环境的合成账号（1 审核员 + 2 企业）")
    parser.add_argument("--out", type=Path, required=True, help="凭证文件路径（写成 0600）")
    parser.add_argument("--force", action="store_true", help="凭证文件已存在时覆盖")
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

    # 连不上库时连接池会在 close() 时再喊一句「couldn't stop thread」——那句话不说原因。
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

    database = Database(dsn, max_size=1)
    try:
        accounts = AccountsRepository(database)
        items, problems = _plan(accounts, reset_passwords=args.reset_passwords)
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
            print(
                f"{len(items)} 个合成账号都已存在且身份相符，本轮没有新口令："
                f"{args.out} 未改动。要重发口令加 --reset-passwords。"
            )
            return 0

        out_path = args.out
        try:
            with CredentialFile(out_path, force=args.force) as out:
                try:
                    created, skipped = _provision(accounts, items, out)
                except Exception:
                    where = out.tmp_path if args.force else out_path
                    print(
                        f"建号中途失败：已生成 {out.lines_written} 条口令，写在 {where}（0600）。"
                        "把它收好再用 --force 重跑，或换一个 --out 路径——已存在的账号会跳过，"
                        "不会重复建；缺的那几个会补上。",
                        file=sys.stderr,
                    )
                    raise
        except Exception as exc:  # 不打印异常详情：psycopg 的报错里带主机与库名
            print(
                f"建账号失败（{type(exc).__name__}）：请确认迁移 009 已执行、"
                "DATABASE_URL 指向本环境的库、账号有写权限。",
                file=sys.stderr,
            )
            return 3
    finally:
        database.close()

    print(
        f"已在 {len(SPECS)} 个合成账号上处理完成：新建/重置 {created} 个，"
        f"已存在跳过 {skipped} 个。口令只写进 {args.out}（0600），未回显。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

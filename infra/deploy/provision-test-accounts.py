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

用法（ECS，root；``DATABASE_URL`` 从 ``api.env`` 读，不上命令行）::

    systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
        /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/provision-test-accounts.py \\
        --out /root/huice-test-accounts-$(date +%Y%m%d).txt

本地（仓库根目录）::

    DATABASE_URL=... services/api/.venv/Scripts/python.exe \\
        infra/deploy/provision-test-accounts.py --out ./.local-test-accounts.txt

凭证文件只走机密渠道交付（当面对传、私聊、受控网盘），**不进仓库、不进文档、不进聊天、
不进 PR**；交付后按 §3.6 的做法登录验证，用完即删。这些账号是联调账号，不是验收证据。

退出码：0 成功（含「账号已存在、本次跳过」）；2 环境变量或参数不合法（只报变量名）；
3 写库失败；4 凭证文件已存在且没给 ``--force``。
"""

from __future__ import annotations

import argparse
import logging
import os
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


def _check_out_path(path: Path, *, force: bool) -> bool:
    """建库**之前**先确认凭证文件写得下去。

    反过来做会留下「账号建好了、口令没落盘」的账号——口令只在这里出现一次，丢了就只能
    ``--reset-passwords`` 重发，那一步又要求先知道账号已经存在。
    """

    if path.exists() and not force:
        print(
            f"凭证文件 {path} 已存在：加 --force 覆盖，或换一个 --out 路径。"
            "覆盖会重写本次生成的口令，请确认旧文件没有被谁拿着用。",
            file=sys.stderr,
        )
        return False
    parent = path.parent
    if not parent.exists():
        print(f"凭证文件所在目录不存在：{parent}", file=sys.stderr)
        return False
    return True


def _write_credentials(path: Path, lines: list[str]) -> None:
    """写 0600 的凭证文件（调用前已确认可写）。"""

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    # O_TRUNC 不会重设权限：存在的文件可能是 0644，这里统一压到 0600。
    os.chmod(path, 0o600)


def _provision(
    accounts: AccountsRepository, *, reset_passwords: bool
) -> tuple[list[str], list[str]]:
    """建账号；返回 (新凭证行, 已存在账号的用户名)。

    口令只在返回的凭证行里出现一次，调用方写文件用——不回显、不日志。
    """

    lines: list[str] = []
    existing: list[str] = []
    for spec in SPECS:
        row = accounts.find_user_by_username(spec.username)
        password = _generate_password()

        if row is not None:
            existing.append(spec.username)
            if reset_passwords:
                accounts.update_password(
                    user_id=row["id"], password_hash=auth_tools.hash_password(password)
                )
                lines.append(_credential_line(spec, password, note="口令已重置"))
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
            existing.append(spec.username)
            continue
        lines.append(_credential_line(spec, password, note="新建"))
    return lines, existing


def _credential_line(spec: AccountSpec, password: str, *, note: str) -> str:
    parts = [f"role={spec.role}", f"username={spec.username}", f"password={password}"]
    if spec.is_enterprise:
        parts.append(f"enterprise={spec.name_synthetic}")
    parts.append(f"({note})")
    return " ".join(parts)


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

    # 连不上库时连接池会在 close() 时再喊一句「couldn't stop thread」——那句话不说原因，
    # 真正的原因由下面统一打印（库不可达、迁移 009 未执行、无写权限）。
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)

    database = Database(dsn, max_size=1)
    try:
        lines, existing = _provision(
            AccountsRepository(database), reset_passwords=args.reset_passwords
        )
    except Exception as exc:  # 不打印异常详情：psycopg 的报错里带主机与库名
        print(
            f"建账号失败（{type(exc).__name__}）：请确认迁移 009 已执行、"
            "DATABASE_URL 指向本环境的库、账号有写权限。",
            file=sys.stderr,
        )
        return 3
    finally:
        database.close()

    header = [
        "# 惠策协同 · 隔离环境合成账号（仅供 E1/E2 同版联调，不是验收证据）",
        "# 交付走机密渠道；不要入库、不要贴进 PR/文档/聊天。用完按 infra/deploy/README.md §3.6 清理。",
    ]
    _write_credentials(args.out, header + lines)

    print(
        f"已在 {len(SPECS)} 个合成账号上处理完成：新建/重置 {len(lines)} 个，"
        f"已存在跳过 {len(existing)} 个。口令只写进 {args.out}（0600），未回显。"
    )
    if existing:
        print(
            "已存在的账号没有改口令：" + "、".join(existing) + "。要重发口令请加 --reset-passwords。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

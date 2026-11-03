"""``infra/deploy/enable-tls.sh`` 的配置回滚与 SAN 判定。

部署脚本没有服务端可以打，这里用 bash 载入函数、把路径指到临时目录，把两条容易
踩的路径各走一遍：首次安装（原本没有 443 配置）和已有配置重跑（nginx -t 失败时
必须回到原来那份，而不是把文件删掉）。脚本有 ``BASH_SOURCE == $0`` 守卫，载入时
不会执行 main，也不会碰真实的 /etc/nginx。

脚本先复制到临时目录再载入：仓库路径里有中文，Windows 上的 MSYS 会在传递路径给
原生命令时按代码页重编码，导致「文件明明在却 stat 不到」。临时目录是纯 ASCII，
两边环境一致。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = REPO_ROOT / "infra" / "deploy"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="需要 bash 才能测部署脚本")


@pytest.fixture()
def deploy(tmp_path: Path) -> dict[str, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("enable-tls.sh", "nginx-huice-tls.conf"):
        shutil.copyfile(DEPLOY_DIR / name, scripts / name)
    return {
        "script": scripts / "enable-tls.sh",
        "conf_source": scripts / "nginx-huice-tls.conf",
        "conf": tmp_path / "conf.d" / "huice-api-tls.conf",
        "conf_dir": tmp_path / "conf.d",
        "tls_dir": tmp_path / "tls",
    }


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        # 脚本的日志是中文：Windows 上 subprocess 默认按 GBK 解码会炸，显式指定 UTF-8。
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _harness(deploy: dict[str, Path], body: list[str]) -> str:
    deploy["conf_dir"].mkdir(parents=True, exist_ok=True)
    return "\n".join(
        [
            "set -e",
            f'export HUICE_TLS_NGINX_CONF="{deploy["conf"].as_posix()}"',
            f'export HUICE_TLS_DIR="{deploy["tls_dir"].as_posix()}"',
            f'source "{deploy["script"].as_posix()}"',
            *body,
        ]
    )


def test_first_install_removes_new_conf_on_failure(deploy):
    """原本没有 443 配置：nginx -t 失败后不能留下半份配置。"""

    result = _bash(
        _harness(
            deploy,
            [
                'test ! -e "$NGINX_CONF"',
                "install_conf",
                'test -f "$NGINX_CONF"',
                f'cmp -s "$NGINX_CONF" "{deploy["conf_source"].as_posix()}"',
                "rollback_conf",
                'test ! -e "$NGINX_CONF"',
                'test ! -e "$BACKUP_FILE"',
                "echo rolled-back",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "rolled-back" in result.stdout
    assert not deploy["conf"].exists()


def test_rerun_restores_previous_conf_on_failure(deploy):
    """已有 443 配置：失败后必须恢复原来那份，而不是删掉（M2 P1）。"""

    deploy["conf_dir"].mkdir(parents=True, exist_ok=True)
    previous = "server { listen 443 ssl; # 原有的 443 站点\n}\n"
    deploy["conf"].write_text(previous, encoding="utf-8")

    result = _bash(
        _harness(
            deploy,
            [
                "install_conf",
                'test -f "$BACKUP_FILE"',
                f'cmp -s "$NGINX_CONF" "{deploy["conf_source"].as_posix()}"',
                "rollback_conf",
                'test ! -e "$BACKUP_FILE"',
                'cat "$NGINX_CONF"',
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(previous)
    assert deploy["conf"].read_text(encoding="utf-8") == previous


def test_san_uses_ip_for_addresses_and_dns_for_names(deploy):
    """Chrome/Edge 只认 SAN：IP 要写 ``IP:``，域名要写 ``DNS:``。"""

    result = _bash(
        _harness(
            deploy,
            [
                'test "$(san_for_host 139.9.33.46)" = "IP:139.9.33.46"',
                'test "$(san_for_host demo.example.com)" = "DNS:demo.example.com"',
                "echo san-ok",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "san-ok" in result.stdout

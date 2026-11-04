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
    for name in ("enable-tls.sh", "nginx-huice-tls.conf", "nginx-huice-site.conf"):
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


# ------------------------------------------------------------ 同源静态站点


def _web(deploy: dict[str, Path]) -> Path:
    """建一份最小前端产物目录（只要 index.html 就够判「装的是哪个模板」）。"""

    root = deploy["conf"].parent.parent / "web-dist"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return root


def test_web_root_installs_the_same_origin_site(deploy):
    """``--web-root`` 装的是「静态文件 + /api/v1 反代」，占位符必须被替换干净。"""

    web = _web(deploy)
    result = _bash(
        _harness(
            deploy,
            [
                f'WEB_ROOT="{web.as_posix()}"',
                "install_conf",
                '! grep -q "__WEB_ROOT__" "$NGINX_CONF"',
                f'grep -qF "root  {web.as_posix()};" "$NGINX_CONF"',
                'grep -q "location /api/v1/" "$NGINX_CONF"',
                "grep -qF 'try_files $uri $uri/ /index.html' \"$NGINX_CONF\"",
                "echo site-ok",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "site-ok" in result.stdout


def test_without_web_root_the_api_only_site_is_installed(deploy):
    """不给 ``--web-root`` 时仍装原来的「全部反代」，不引入静态文件行为。"""

    result = _bash(
        _harness(
            deploy,
            [
                "install_conf",
                '! grep -q "location /api/v1/" "$NGINX_CONF"',
                '! grep -q "try_files" "$NGINX_CONF"',
                'grep -qF "proxy_pass http://127.0.0.1:8000;" "$NGINX_CONF"',
                "echo api-ok",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "api-ok" in result.stdout


def test_downgrade_from_site_to_api_only_is_blocked_without_force(deploy):
    """已装同源站点后不给 ``--web-root`` 重跑，会把 443 切回仅反代 API，先拦下。"""

    web = _web(deploy)
    result = _bash(
        _harness(
            deploy,
            [
                f'WEB_ROOT="{web.as_posix()}"',
                "install_conf",
                'WEB_ROOT=""',
                "FORCE=0",
                "guard_web_root_downgrade",
            ],
        )
    )

    assert result.returncode != 0
    assert "同源站点" in result.stderr
    assert web.as_posix() in result.stderr  # 提示里给出可以直接复制的 --web-root
    assert "--force" in result.stderr
    # 被拦下时配置原样未动，仍是同源站点
    assert "try_files" in deploy["conf"].read_text(encoding="utf-8")


def test_downgrade_allowed_with_force_and_warns(deploy):
    """显式 ``--force`` 才切回仅反代 API，并且要留下警告。"""

    web = _web(deploy)
    result = _bash(
        _harness(
            deploy,
            [
                f'WEB_ROOT="{web.as_posix()}"',
                "install_conf",
                'WEB_ROOT=""',
                "FORCE=1",
                "guard_web_root_downgrade",
                "install_conf",
                '! grep -q "try_files" "$NGINX_CONF"',
                "echo switched-to-api",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "警告" in result.stdout
    assert "switched-to-api" in result.stdout


def test_guard_is_noop_when_the_installed_site_is_api_only(deploy):
    """原本就是仅反代 API：没有可回退的站点形态，不该拦。"""

    result = _bash(
        _harness(
            deploy,
            [
                "install_conf",
                'WEB_ROOT=""',
                "FORCE=0",
                "guard_web_root_downgrade",
                "echo api-noop",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "api-noop" in result.stdout


def test_guard_is_noop_when_reinstalling_the_site_with_web_root(deploy):
    """带着 ``--web-root`` 重装同源站点（换产物、续证书）是正常路径，不该拦。"""

    web = _web(deploy)
    result = _bash(
        _harness(
            deploy,
            [
                "install_conf",  # 先装成仅 API，确保被拦的原因只可能是「没给 --web-root」
                f'WEB_ROOT="{web.as_posix()}"',
                "FORCE=0",
                "guard_web_root_downgrade",
                "install_conf",
                "grep -qF 'try_files $uri $uri/ /index.html' \"$NGINX_CONF\"",
                "echo site-noop",
            ],
        )
    )

    assert result.returncode == 0, result.stderr
    assert "site-noop" in result.stdout


def test_validate_web_root_rejects_paths_nginx_cannot_take(deploy):
    """目录会被原样写进 nginx 的 ``root`` 并经过 sed 替换，非保守字符集一律先拒。"""

    relative = _bash(_harness(deploy, ['validate_web_root "web/dist"']))
    assert relative.returncode != 0
    assert "绝对路径" in relative.stderr

    for bad in ("/srv/huice|web", "/srv/huice&web", "/srv/huice web", "/srv/huice;web"):
        rejected = _bash(_harness(deploy, [f'validate_web_root "{bad}"']))
        assert rejected.returncode != 0, bad
        assert "只能用字母" in rejected.stderr, bad

    ok = _bash(_harness(deploy, ['validate_web_root "/srv/huice/web"', "echo ok"]))
    assert ok.returncode == 0, ok.stderr


#!/usr/bin/env bash
# Purpose: 在华为云 ECS（Ubuntu 24.04）上把已上传的代码装成 systemd 服务。
# Call:   sudo bash infra/deploy/provision.sh [代码根目录]
#         代码根目录默认 /opt/huice/app，需已包含 services/api 与 contracts。
# Output: 安装进度与下一步提示；**不打印任何密钥**，配置文件留给人工填写。
# 约束:   可重复执行；已存在的 /etc/huice/api.env 不会被覆盖。
set -euo pipefail

APP_ROOT="${1:-/opt/huice/app}"
APP_USER="huice"
VENV_DIR="/opt/huice/venv"
ENV_DIR="/etc/huice"
ENV_FILE="${ENV_DIR}/api.env"
PIP_INDEX="${PIP_INDEX_URL:-https://repo.huaweicloud.com/repository/pypi/simple}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[provision] %s\n' "$*"; }
die() { printf '[provision] 失败: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行（sudo bash infra/deploy/provision.sh）"
[[ -f "${APP_ROOT}/services/api/requirements.lock" ]] || die "未找到 ${APP_ROOT}/services/api/requirements.lock，请先上传代码"

log "1/6 安装系统包（python3-venv、nginx、编译工具）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# build-essential/python3-dev 是给 crcmod 准备的：PyPI 没有它的 manylinux wheel，
# pip 只能从源码编译，缺 gcc 会在装 esdk-obs-python 时失败。
apt-get install -y -qq python3-venv python3-dev build-essential nginx >/dev/null

log "2/6 创建运行用户 ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --home /opt/huice --shell /usr/sbin/nologin "${APP_USER}"
fi
mkdir -p /opt/huice

log "3/6 建立虚拟环境并安装锁定依赖（镜像：${PIP_INDEX}）"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --upgrade pip --index-url "${PIP_INDEX}" >/dev/null
"${VENV_DIR}/bin/python" -m pip install -r "${APP_ROOT}/services/api/requirements.lock" --index-url "${PIP_INDEX}"
"${VENV_DIR}/bin/python" -m pip check

log "4/6 安装 systemd unit 与 nginx 配置"
install -m 0644 "${SCRIPT_DIR}/huice-api.service" /etc/systemd/system/huice-api.service
install -m 0644 "${SCRIPT_DIR}/nginx-huice.conf" /etc/nginx/conf.d/huice-api.conf
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
chown -R "${APP_USER}:${APP_USER}" /opt/huice

log "5/6 准备环境变量文件 ${ENV_FILE}"
mkdir -p "${ENV_DIR}"
chmod 750 "${ENV_DIR}"
if [[ -f "${ENV_FILE}" ]]; then
    log "已存在 ${ENV_FILE}，保持不动"
else
    cat > "${ENV_FILE}" <<'EOF'
# 由 infra/deploy/provision.sh 生成；填好后 chmod 640，属主 root:huice。
# 缺失必填项时服务启动失败，日志只报变量名，不报值。
APP_ENV=demo
API_HOST=127.0.0.1
API_PORT=8000
DATABASE_URL=
REDIS_URL=
CORS_ORIGINS=
MODEL_MODE=fixture
OBJECT_STORE_MODE=local
LOCAL_OBJECT_DIR=.local-objects
ADMIN_TOKEN=
DEMO_ACCESS_MODE=restricted
# MODEL_MODE=deepseek 时以下三项必填；fixture 模式留空。
DEEPSEEK_BASE_URL=
DEEPSEEK_MODEL=
DEEPSEEK_API_KEY=
OBS_BUCKET=
OBS_ENDPOINT=
OBS_AK=
OBS_SK=
EOF
    log "已生成模板，请填写 DATABASE_URL / REDIS_URL / ADMIN_TOKEN / CORS_ORIGINS 等"
fi
chown root:"${APP_USER}" "${ENV_FILE}"
chmod 640 "${ENV_FILE}"

log "6/6 启用服务"
nginx -t
systemctl daemon-reload
systemctl enable --now nginx >/dev/null
systemctl enable huice-api >/dev/null
# 配置未填写时首启会失败，这里不把它当作安装失败，只提示查看日志。
systemctl restart huice-api || true

cat <<EOF

[provision] 完成。下一步：
  1) 编辑 ${ENV_FILE}，填入 DATABASE_URL、REDIS_URL、ADMIN_TOKEN、CORS_ORIGINS
     （OBJECT_STORE_MODE=obs 时还需 OBS_BUCKET/OBS_ENDPOINT/OBS_AK/OBS_SK）
  2) sudo systemctl restart huice-api
  3) curl -i http://127.0.0.1:8000/api/v1/health        # 期望 200
  4) journalctl -u huice-api -n 50 --no-pager           # 失败时先看这里
EOF

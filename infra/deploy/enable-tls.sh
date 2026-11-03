#!/usr/bin/env bash
# Purpose: 为评审演示临时启用自签 HTTPS（ICP 备案前的替代方案）。
# Call:   sudo bash infra/deploy/enable-tls.sh <公网IP或域名> [--force]
# Output: 证书路径、有效期、nginx 重载结果与验证命令；**不打印私钥**。
# 约束:   可重复执行；已有证书默认保留，--force 才重新签发（换 EIP 时用）。
#         证书与私钥只落 /etc/huice/tls，永不进仓库、不进日志。
#         装配置前先备份同名的既有配置；nginx -t 失败时恢复备份（没有备份就删掉新装的），
#         不能把机器留在「原配置没了、新配置又不通」的状态。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLS_DIR="${HUICE_TLS_DIR:-/etc/huice/tls}"
CRT_FILE="${TLS_DIR}/huice.crt"
KEY_FILE="${TLS_DIR}/huice.key"
NGINX_CONF="${HUICE_TLS_NGINX_CONF:-/etc/nginx/conf.d/huice-api-tls.conf}"
# 备份放在同目录：nginx 只读 *.conf，多出来的 .bak 不会被当成站点配置加载。
BACKUP_FILE="${NGINX_CONF}.bak"
DAYS="${TLS_DAYS:-365}"

log() { printf '[tls] %s\n' "$*"; }
die() { printf '[tls] 失败: %s\n' "$*" >&2; exit 1; }

# Chrome/Edge 只认 SAN，不看 CN：只写 CN 的证书会被判「名称不匹配」，过警告都难。
san_for_host() {
    if [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        printf 'IP:%s' "$1"
    else
        printf 'DNS:%s' "$1"
    fi
}

# 覆盖前先备份：重跑时若 nginx -t 失败，回滚要能回到机器上原来那份配置。
install_conf() {
    if [[ -f "${NGINX_CONF}" ]]; then
        cp -p "${NGINX_CONF}" "${BACKUP_FILE}"
        log "已备份既有配置到 ${BACKUP_FILE}"
    else
        rm -f "${BACKUP_FILE}"
    fi
    install -m 0644 "${SCRIPT_DIR}/nginx-huice-tls.conf" "${NGINX_CONF}"
}

rollback_conf() {
    if [[ -f "${BACKUP_FILE}" ]]; then
        mv -f "${BACKUP_FILE}" "${NGINX_CONF}"
        log "nginx -t 未通过，已恢复原配置 ${NGINX_CONF}"
    else
        rm -f "${NGINX_CONF}"
        log "nginx -t 未通过，已移除新装配置（原本不存在）"
    fi
}

main() {
    HOST_ARG=""
    FORCE=0
    for arg in "$@"; do
        case "${arg}" in
            --force) FORCE=1 ;;
            -*) die "未知参数 ${arg}（可用：--force）" ;;
            *) [[ -z "${HOST_ARG}" ]] || die "只能给一个主机名或 IP"; HOST_ARG="${arg}" ;;
        esac
    done

    [[ -n "${HOST_ARG}" ]] || die "用法：sudo bash infra/deploy/enable-tls.sh <公网IP或域名> [--force]"
    [[ $EUID -eq 0 ]] || die "请用 root 运行（sudo bash infra/deploy/enable-tls.sh ...）"
    case "${HOST_ARG}" in
        */*|*" "*|*:*) die "主机参数不能含空格、路径或端口：${HOST_ARG}" ;;
    esac
    command -v openssl >/dev/null || die "未找到 openssl，请先 apt-get install -y openssl"

    SAN="$(san_for_host "${HOST_ARG}")"

    if [[ -f "${CRT_FILE}" && -f "${KEY_FILE}" && ${FORCE} -eq 0 ]]; then
        log "已存在证书，保留不动（换 IP 或要重签时加 --force）"
        # openssl 输出的是 `IP Address:1.2.3.4` / `DNS:example.com`，和传入值格式不同，
        # 所以按「是否包含传入地址」判断，而不是逐字相等。
        existing_san="$(openssl x509 -in "${CRT_FILE}" -noout -ext subjectAltName 2>/dev/null \
            | tail -n +2 | tr -d ' ' || true)"
        if [[ -n "${existing_san}" && "${existing_san}" != *"${HOST_ARG}"* ]]; then
            log "警告：现有证书主体为 ${existing_san}，与本次传入的 ${HOST_ARG} 不一致；需要重签请加 --force"
        fi
    else
        log "签发自签证书（${SAN}，${DAYS} 天）"
        install -d -m 700 "${TLS_DIR}"
        # 私钥在生成阶段就只对属主可读，避免中间态被别的进程读到。
        umask 077
        cfg="$(mktemp)"
        trap 'rm -f "${cfg}"' EXIT
        cat > "${cfg}" <<EOF
[req]
prompt = no
distinguished_name = dn
x509_extensions = v3
[dn]
CN = ${HOST_ARG}
[v3]
subjectAltName = ${SAN}
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF
        # 先写临时文件再原子替换：签一半失败时旧证书仍然可用。
        openssl req -x509 -nodes -newkey rsa:2048 -days "${DAYS}" \
            -keyout "${KEY_FILE}.new" -out "${CRT_FILE}.new" -config "${cfg}" 2>/dev/null
        chmod 600 "${KEY_FILE}.new"
        chmod 644 "${CRT_FILE}.new"
        mv -f "${KEY_FILE}.new" "${KEY_FILE}"
        mv -f "${CRT_FILE}.new" "${CRT_FILE}"
        log "证书就绪：${CRT_FILE}（私钥 ${KEY_FILE}，权限 600）"
    fi

    log "安装 nginx 443 站点配置"
    install_conf
    if [[ ! -f /etc/nginx/conf.d/huice-api.conf ]]; then
        log "注意：未找到 /etc/nginx/conf.d/huice-api.conf，80 端口的站点尚未安装（先跑 provision.sh）"
    fi

    if ! nginx -t; then
        rollback_conf
        die "nginx -t 未通过，已回到安装前的状态，80 端口的现有站点不受影响"
    fi
    systemctl enable nginx >/dev/null
    # 与 provision.sh 同一处置：nginx 常已被 apt 拉起，只 enable 不会重载新配置。
    systemctl reload-or-restart nginx
    rm -f "${BACKUP_FILE}"
    log "nginx 已重载"

    log "证书有效期：$(openssl x509 -in "${CRT_FILE}" -noout -enddate | cut -d= -f2)"

    cat <<EOF

[tls] 完成。下一步：
  1) 确认华为云安全组入方向有 TCP 443（0.0.0.0/0），没有的话外网超时
  2) ECS 本机：curl -k -i https://127.0.0.1/api/v1/health          # 期望 200
  3) 你自己电脑：curl -k -i https://${HOST_ARG}/api/v1/health      # 期望 200 + X-Trace-Id
  4) 浏览器打开 https://${HOST_ARG} —— 高级 → 继续前往，过一次自签警告
  5) 会话 cookie（restricted 模式）：由受控入口写入并带 HttpOnly，见 README §3.2
EOF
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

#!/usr/bin/env bash
# Purpose: 为评审演示临时启用自签 HTTPS（ICP 备案前的替代方案）。
# Call:   sudo bash infra/deploy/enable-tls.sh <公网IP或域名> [--force] [--web-root <前端 dist 目录>]
# Output: 证书路径、站点形态、有效期、nginx 重载结果与验证命令；**不打印私钥**。
# 约束:   可重复执行；已有证书默认保留，--force 才重新签发（换 EIP 时用）。
#         证书与私钥只落 /etc/huice/tls，永不进仓库、不进日志。
#         装配置前先备份同名的既有配置；nginx -t 失败时恢复备份（没有备份就删掉新装的），
#         不能把机器留在「原配置没了、新配置又不通」的状态。
#         443 只装**一份**站点配置：给了 --web-root 就装「静态文件 + /api/v1 同源反代」，
#         不给就装「全部反代 API」。两种形态都写到同一个文件名，重跑脚本即切换，
#         不会出现两个 `server_name _` 抢 443 的 conflicting server name。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLS_DIR="${HUICE_TLS_DIR:-/etc/huice/tls}"
CRT_FILE="${TLS_DIR}/huice.crt"
KEY_FILE="${TLS_DIR}/huice.key"
NGINX_CONF="${HUICE_TLS_NGINX_CONF:-/etc/nginx/conf.d/huice-api-tls.conf}"
# 备份放在同目录：nginx 只读 *.conf，多出来的 .bak 不会被当成站点配置加载。
BACKUP_FILE="${NGINX_CONF}.bak"
DAYS="${TLS_DAYS:-365}"
API_TEMPLATE="nginx-huice-tls.conf"
SITE_TEMPLATE="nginx-huice-site.conf"
# 空 = 只反代 API；由 main 按 --web-root 设置。install_conf 也读它，测试可直接赋值。
WEB_ROOT=""

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

conf_template() {
    if [[ -n "${WEB_ROOT}" ]]; then
        printf '%s' "${SITE_TEMPLATE}"
    else
        printf '%s' "${API_TEMPLATE}"
    fi
}

# 前端目录会被替换进 nginx 的 `root` 指令，要挡住会破坏语法或模板的字符。
validate_web_root() {
    local path="$1"
    [[ "${path}" == /* ]] || die "前端目录要写成绝对路径：${path}"
    # 只放行保守字符集：空格/分号会破坏 nginx 语法，竖线与 & 会被 sed 当成分隔符或替换标记。
    [[ "${path}" != *[!A-Za-z0-9/_.-]* ]] || die "前端目录只能用字母、数字和 / _ . -：${path}"
}

# 覆盖前先备份：重跑时若 nginx -t 失败，回滚要能回到机器上原来那份配置。
install_conf() {
    local template="${SCRIPT_DIR}/$(conf_template)"
    [[ -f "${template}" ]] || die "缺少站点配置模板：${template}"
    if [[ -f "${NGINX_CONF}" ]]; then
        cp -p "${NGINX_CONF}" "${BACKUP_FILE}"
        log "已备份既有配置到 ${BACKUP_FILE}"
    else
        rm -f "${BACKUP_FILE}"
    fi
    # 先渲染到同目录的临时文件再原子替换：渲染失败时线上那份配置仍然可用。
    local rendered="${NGINX_CONF}.new"
    if [[ -n "${WEB_ROOT}" ]]; then
        sed "s|__WEB_ROOT__|${WEB_ROOT}|g" "${template}" > "${rendered}"
        if grep -q '__WEB_ROOT__' "${rendered}"; then
            rm -f "${rendered}" "${BACKUP_FILE}"
            die "模板替换后仍残留 __WEB_ROOT__ 占位符：${template}"
        fi
    else
        cp -p "${template}" "${rendered}"
    fi
    chmod 0644 "${rendered}"
    mv -f "${rendered}" "${NGINX_CONF}"
    log "已安装 $(conf_template) → ${NGINX_CONF}"
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
    WEB_ROOT=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --force) FORCE=1 ;;
            --web-root)
                [[ $# -ge 2 && -n "$2" ]] || die "--web-root 需要一个目录参数"
                WEB_ROOT="$2"
                shift
                ;;
            --web-root=*) WEB_ROOT="${1#--web-root=}" ;;
            -*) die "未知参数 $1（可用：--force、--web-root <前端 dist 目录>）" ;;
            *) [[ -z "${HOST_ARG}" ]] || die "只能给一个主机名或 IP"; HOST_ARG="$1" ;;
        esac
        shift
    done

    [[ -n "${HOST_ARG}" ]] || die "用法：sudo bash infra/deploy/enable-tls.sh <公网IP或域名> [--force] [--web-root <前端 dist 目录>]"
    [[ $EUID -eq 0 ]] || die "请用 root 运行（sudo bash infra/deploy/enable-tls.sh ...）"
    case "${HOST_ARG}" in
        */*|*" "*|*:*) die "主机参数不能含空格、路径或端口：${HOST_ARG}" ;;
    esac
    command -v openssl >/dev/null || die "未找到 openssl，请先 apt-get install -y openssl"

    if [[ -n "${WEB_ROOT}" ]]; then
        validate_web_root "${WEB_ROOT}"
        [[ -d "${WEB_ROOT}" ]] || die "前端目录不存在：${WEB_ROOT}（先把 apps/web 的 dist 拷过去）"
        if [[ ! -f "${WEB_ROOT}/index.html" ]]; then
            log "注意：${WEB_ROOT} 下没有 index.html，站点装好后会 404，记得补上构建产物"
        fi
        # 同源的前提是前端构建时就把 API 指到相对路径，否则页面仍会去连本机 8000。
        log "前端同源：构建时须设 VITE_API_BASE_URL=/api/v1（apps/web 由 M2 构建）"
    fi

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

    if [[ -n "${WEB_ROOT}" ]]; then
        log "安装 443 站点配置：前端静态文件（${WEB_ROOT}）+ /api/v1 同源反代"
    else
        log "安装 443 站点配置：仅反代 API"
    fi
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

    if [[ -n "${WEB_ROOT}" ]]; then
        SITE_LINE="  4) 浏览器打开 https://${HOST_ARG}/ —— 高级 → 继续前往，应看到前端首页"
    else
        SITE_LINE="  4) 浏览器打开 https://${HOST_ARG}/api/v1/health —— 高级 → 继续前往（当前站点只反代 API）"
    fi

    cat <<EOF

[tls] 完成。下一步：
  1) 确认华为云安全组入方向有 TCP 443（0.0.0.0/0），没有的话外网超时
  2) ECS 本机：curl -k -i https://127.0.0.1/api/v1/health          # 期望 200
  3) 你自己电脑：curl -k -i https://${HOST_ARG}/api/v1/health      # 期望 200 + X-Trace-Id
${SITE_LINE}
  5) 会话 cookie（restricted 模式）：由受控入口写入并带 HttpOnly，见 README §3.2
EOF
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

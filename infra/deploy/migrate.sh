#!/usr/bin/env bash
# 执行 001—007 迁移与验收查询。取值走 systemd 自己的解析，不复制它的语法：
#
#   1) 服务在跑：读 /proc/<MainPID>/environ——那是 systemd 真正传进进程的那份值，
#      与它加载 EnvironmentFile 的规则无关，天然一致；
#   2) 服务没跑：让 systemd 自己按 EnvironmentFile 解析一次（systemd-run --pipe），
#      语法、引号、续行全由 systemd 决定，本脚本不解释文件内容；
#   3) 两条都不行：退出码非零，不猜、不退回默认连接参数。
#
# 守卫：读不到、读到空值、任一迁移失败，都在下一步之前停下——没迁移完的库上不会跑验收查询。
#
# 仅供测试/特殊部署覆盖的变量（生产不需要设置）：
#   HUICE_SERVICE      unit 名，默认 huice-api
#   HUICE_ENV_FILE     EnvironmentFile 路径，默认 /etc/huice/api.env
#   HUICE_SQL_DIR      SQL 目录，默认脚本同级的 ../sql
#   HUICE_SYSTEMCTL    systemctl 可执行文件
#   HUICE_SYSTEMD_RUN  systemd-run 可执行文件
#   HUICE_PSQL         psql 可执行文件
set -euo pipefail

SERVICE=${HUICE_SERVICE:-huice-api}
ENV_FILE=${HUICE_ENV_FILE:-/etc/huice/api.env}
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SQL_DIR=${HUICE_SQL_DIR:-$HERE/../sql}
SYSTEMCTL=${HUICE_SYSTEMCTL:-systemctl}
SYSTEMD_RUN=${HUICE_SYSTEMD_RUN:-systemd-run}
PSQL=${HUICE_PSQL:-psql}

# 顺序即依赖顺序。增删迁移时必须同时改这里与 infra/sql/ 下的文件；
# services/api/tests/test_migrate_script.py 有一条守卫用例比对两者，漏改会转红。
MIGRATIONS=(
    001_init
    002_evaluation_and_idempotency
    003_trace_step_details
    004_invalidation_event_period
    005_fact_dictionary_extension
    006_fact_dictionary_extension_rc4
    007_idempotency_usage_semantics
)

# 只报变量名与原因：连接串里有口令，绝不能回显。
die() {
    printf 'migrate.sh: %s\n' "$*" >&2
    exit 1
}

read_env_var() {
    local name=$1 pid value

    case $name in
        '' | [0-9]* | *[!A-Za-z0-9_]*) die "内部错误：变量名 $name 不合法。" ;;
    esac

    # 1) 服务在跑：取它环境里 systemd 实际传进去的值，不经过任何自己写的解析器。
    pid=$("$SYSTEMCTL" show -p MainPID --value "$SERVICE" 2>/dev/null) || pid=""
    pid=$(printf '%s' "$pid" | tr -d '[:space:]')  # 换行会让下面的数字判断落空
    case $pid in
        '' | 0 | *[!0-9]*) ;;
        *)
            if [ -r "/proc/$pid/environ" ]; then
                # 同名变量在进程环境里只会有一份；不用 head 提前关管道，避免 SIGPIPE 撞上 pipefail。
                value=$(tr '\0' '\n' <"/proc/$pid/environ" | sed -n "s/^${name}=//p")
                value=${value%%$'\n'*}
                if [ -n "$value" ]; then
                    printf '%s' "$value"
                    return 0
                fi
                # 进程环境里没有这个变量（或为空）：不能就此断定「没配置」，继续问 systemd。
            fi
            ;;
    esac

    # 2) 服务没跑：让 systemd 按 EnvironmentFile 的规则解析一次。--pipe 把 stdout 交回来，
    #    --wait 传递退出码，--collect 跑完即回收这个临时单元。
    "$SYSTEMD_RUN" --quiet --pipe --wait --collect \
        -p "EnvironmentFile=$ENV_FILE" /bin/sh -c "printf '%s' \"\${$name-}\""
}

DB_URL=$(read_env_var DATABASE_URL) ||
    die "读不到 DATABASE_URL（服务未运行，systemd-run 也读不了 $ENV_FILE）。停止：不执行任何迁移。"
[ -n "$DB_URL" ] || die "DATABASE_URL 为空。停止：不执行任何迁移（空串会让 psql 退回默认参数）。"

for migration in "${MIGRATIONS[@]}"; do
    printf 'migrate.sh: 应用 %s\n' "$migration" >&2
    "$PSQL" "$DB_URL" -v ON_ERROR_STOP=1 -f "$SQL_DIR/$migration.sql" ||
        die "迁移 $migration 失败。停止：后续迁移与验收查询都不执行。"
done

printf 'migrate.sh: 验收查询 schema_migration\n' >&2
applied=$("$PSQL" "$DB_URL" -tAc "SELECT id FROM schema_migration ORDER BY id") ||
    die "验收查询失败：读不到 schema_migration。"

missing=""
rows=0
for migration in "${MIGRATIONS[@]}"; do
    case $'\n'"$applied"$'\n' in
        *$'\n'"$migration"$'\n'*) rows=$((rows + 1)) ;;
        *) missing="$missing $migration" ;;
    esac
done
[ -z "$missing" ] || die "schema_migration 里缺:$missing。请先查上面的迁移输出。"

printf '%s\n' "$applied" >&2
printf 'migrate.sh: 本脚本检查的 %s 个迁移 id 均已登记在 schema_migration。\n' "$rows" >&2

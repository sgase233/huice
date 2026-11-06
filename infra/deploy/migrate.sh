#!/usr/bin/env bash
# 执行清单里**尚未登记**的迁移，再跑验收查询。已登记的跳过、不重放：库已推进时重放
# 旧迁移会撞上后来的约束（005 在库到 008 时重跑即报错），「可重复执行」不等于「能跳过
# 中间状态」。取值走 systemd 自己的解析，不复制它的语法：
#
#   1) 服务在跑：读 /proc/<MainPID>/environ——那是 systemd 真正传进进程的那份值，
#      与它加载 EnvironmentFile 的规则无关，天然一致；
#   2) 服务没跑：让 systemd 自己按 EnvironmentFile 解析一次（systemd-run --pipe），
#      语法、引号、续行全由 systemd 决定，本脚本不解释文件内容；
#   3) 两条都不行：退出码非零，不猜、不退回默认连接参数；并打印 systemd-run 的退出码、
#      stderr 前 10 行与 unit 实际的 EnvironmentFiles/Environment（只打路径与变量名，不打值）。
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
    local name=$1 pid value err_file rc

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
    #
    #    `$` 必须写成 `$$`：systemd 在把命令交给 ExecStart 之前会展开 `${NAME}`，而
    #    `${DATABASE_URL-}` 里的名字带减号、不是合法变量名，会被当成未知变量换成空串——
    #    那时 /bin/sh 收到的是 `printf '%s' ""`，退出码 0、输出为空，看着像「变量没配」。
    #    `$$` 让 systemd 只还回一个字面 `$`，由 /bin/sh 自己去算 `${NAME-}`。
    #    （2026-11-06 云端实测：未转义时 0 字节，转义后取到 54 字节。）
    err_file=$(mktemp) || die "内部错误：无法创建临时文件。"
    value=$("$SYSTEMD_RUN" --quiet --pipe --wait --collect \
        -p "EnvironmentFile=$ENV_FILE" /bin/sh -c "printf '%s' \"\$\${$name-}\"" 2>"$err_file") &&
        rc=0 || rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'migrate.sh: systemd-run 退出码 %s（stderr 前 10 行）：\n' "$rc" >&2
        sed -n '1,10p' "$err_file" | sed 's/^/  | /' >&2 || true
        rm -f "$err_file"
        explain_empty_env
        die "读不到 $name（服务未运行，systemd-run 也读不了 $ENV_FILE）。停止：不执行任何迁移。"
    fi
    rm -f "$err_file"
    [ -n "$value" ] || explain_empty_env
    printf '%s' "$value"
}

# 取到空值时把原因说清楚：**只打印路径与变量名，绝不打印值**。
# 最常见的两种：unit 的 EnvironmentFile 根本不是这个文件；文件里有 systemd 不认的写法
# （`export KEY=value`、等号两侧空格、CRLF/BOM），systemd 会把那行当无效行跳过、不报错。
unit_env_files() {
    # 输出形如 `/etc/huice/api.env (ignore_errors=no)`；可选前缀 `-` 表示文件可以不存在。
    "$SYSTEMCTL" show -p EnvironmentFiles --value "$SERVICE" 2>/dev/null |
        tr ' ' '\n' |
        sed -E 's/^[[:space:]]*//; s/^-//; s/[[:space:]]*\(ignore_errors=[a-z]+\)$//; /^$/d'
}

explain_empty_env() {
    local listed inline
    listed=$(unit_env_files | tr '\n' ' ' || true)  # systemctl 失败时别把整个脚本带崩
    inline=$("$SYSTEMCTL" show -p Environment --value "$SERVICE" 2>/dev/null || true)
    if [ -n "${listed// /}" ]; then
        case " $listed " in
            *" $ENV_FILE "*)
                printf 'migrate.sh: unit %s 的 EnvironmentFiles 含 %s，路径没错；接着查文件内容。\n' \
                    "$SERVICE" "$ENV_FILE" >&2
                ;;
            *)
                printf 'migrate.sh: 注意：unit %s 的 EnvironmentFiles 是 %s，不是 %s——服务未运行时脚本按后者取值，读的是另一个文件。\n' \
                    "$SERVICE" "$listed" "$ENV_FILE" >&2
                ;;
        esac
    elif [ -n "${inline// /}" ]; then
        printf 'migrate.sh: unit %s 没有 EnvironmentFiles，而是用 Environment= 直接声明变量（内容不打印）。\n' \
            "$SERVICE" >&2
    else
        printf 'migrate.sh: unit %s 没有配置 EnvironmentFiles，也没读到 Environment=（unit 是否存在？）。\n' \
            "$SERVICE" >&2
    fi
    printf 'migrate.sh: 自查 %s：每行必须是 KEY=value，不要写 export、等号两侧不要空格、不要 CRLF/BOM；systemd 对这类行只记 journal 警告、取值仍为空。\n' \
        "$ENV_FILE" >&2
}

# read_env_var 失败时自己已经打印了原因（含退出码、stderr 与 unit 的 EnvironmentFiles），
# 这里不再 die 一次：那会把同一句话原样打两遍。它非零退出即「停止」，不执行任何迁移。
DB_URL=$(read_env_var DATABASE_URL) || exit 1
[ -n "$DB_URL" ] || die "DATABASE_URL 为空。停止：不执行任何迁移（空串会让 psql 退回默认参数）。"

# 只跑库里还没登记的迁移。全量重放是危险的：库已推进时 005 会把事实字典约束收回
# 16 键，被已有的 specificMarketSince 行顶掉而报错（2026-11-06 云端实测），于是「有新序号
# 就重跑脚本」根本推不动。先问一次库里已登记了谁，属于幂等的另一面——不重放。
has_table=$("$PSQL" "$DB_URL" -tAc "SELECT to_regclass('public.schema_migration') IS NOT NULL") ||
    die "连得上库，但查不到 schema_migration 是否存在。停止：不执行任何迁移。"
applied=""
if [ "$has_table" = "t" ]; then
    applied=$("$PSQL" "$DB_URL" -tAc "SELECT id FROM schema_migration ORDER BY id") ||
        die "读不到 schema_migration 已登记的迁移。停止：不执行任何迁移。"
fi

# 库里有本脚本清单之外的 id（比如代码比库旧）：不报错，点名即可——那不是本脚本能处理的事。
unknown=""
for id in $applied; do
    case " ${MIGRATIONS[*]} " in
        *" $id "*) ;;
        *) unknown="$unknown $id" ;;
    esac
done
[ -z "$unknown" ] ||
    printf 'migrate.sh: 注意：库里还有本脚本清单之外的迁移:%s（库比代码新，不影响本次执行）。\n' "$unknown" >&2

pending=0
for migration in "${MIGRATIONS[@]}"; do
    case $'\n'"$applied"$'\n' in
        *$'\n'"$migration"$'\n'*)
            printf 'migrate.sh: 已登记，跳过 %s\n' "$migration" >&2
            continue
            ;;
    esac
    pending=$((pending + 1))
    printf 'migrate.sh: 应用 %s\n' "$migration" >&2
    "$PSQL" "$DB_URL" -v ON_ERROR_STOP=1 -f "$SQL_DIR/$migration.sql" ||
        die "迁移 $migration 失败。停止：后续迁移与验收查询都不执行。"
done
printf 'migrate.sh: 待应用的迁移 %s 个。\n' "$pending" >&2

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

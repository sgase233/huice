#!/usr/bin/env bash
# 回滚到上一次更新前的代码树，并把持久对象目录一起带回。
#
# 为什么要有这个脚本：§6.1 更新时把 `.local-objects` **搬**进了新目录，所以回滚时旧树里
# 通常没有这个子目录；「两边都在才合并」这种条件在真实回滚路径上永远是假，更新窗内新
# 导入的对象会全部留在坏目录里（M2 在 PR #44 的 P1）。更麻烦的是：`comm` 只在**失败**
# 时退出非零，有差异照样是 0，写在手册里的「有输出就停下」不会自己发生。这两件事都
# 不能靠读命令的人去记住，只能让命令本身返回非零。
#
# 做法：
#   1) 先判对象存储模式（local 必须有 .local-objects，obs 必须没有），再停服务；
#   2) 旧树（或备份包）摆到 app.stage-$X，**不碰 app**；
#   3) 把 app/.local-objects 里的每个对象并进 stage：缺的复制，同名且内容相同的跳过，
#      同名而内容不同的**一律停下**（不覆盖，交给人决定留哪份）；对象清单取不全也算失败；
#   4) 逐文件比对 sha256 做校验：stage 里必须存在同名同内容的文件，缺一个就非零退出；
#   5) 以上都过了才换目录（app → app.bad-$X，stage → app），最后起服务。
#
# 停服务之后的每一步都有明确的失败处理：**换目录之前**失败就把 stage 收回、服务按原代码
# 重新拉起；**第二步 mv 失败**（app 已经不是原来那棵）就把 app 与旧树都放回去再起服务；
# 恢复动作本身失败会照实打出来，不吞掉。只有「已换完但服务没起来」这一种情况需要人工
# 接手（退出码 5），手册里写了现场在哪。
#
# 用法：rollback.sh <X> [--base DIR] [--from-backup 包] [--object-store local|obs]
#                     [--no-service]
#   X                §6.1 那次实际部署的提交（app.old-$X 里是它之前的版本）
#   --base DIR       部署根，默认 /opt/huice
#   --from-backup    改用更新前的整目录备份包还原（app.old-$X 已不可用时）
#   --object-store   对象存储模式，默认 local。local：app/.local-objects 必须存在；
#                    obs：必须不存在。与 `.local-objects` 实际状态对不上就停在前置检查，
#                    不猜——「目录没了」既可能是 obs，也可能是部署事故，两者不能互相冒充。
#   --no-service     不调用 systemctl（演练/测试用）
#
# 退出码：0 成功；2 前置条件不满足或换目录前失败（现场已恢复）；3 同名对象内容不同、
#         对象放不进去或清单读不全（什么都没换）；4 合并后校验不过（什么都没换）；
#         5 已换完但服务没起来。
#
# 仅供测试/特殊部署覆盖的变量（生产不需要设置）：
#   HUICE_BASE        部署根，默认 /opt/huice
#   HUICE_OWNER       app 的属主，默认 huice:huice
#   HUICE_SERVICE     unit 名，默认 huice-api
#   HUICE_SYSTEMCTL   systemctl 可执行文件
#   HUICE_FORCE_ROOT  置 1 时非 root 也执行 chown（测试 chown 失败分支用）
set -euo pipefail

BASE=${HUICE_BASE:-/opt/huice}
OWNER=${HUICE_OWNER:-huice:huice}
SERVICE=${HUICE_SERVICE:-huice-api}
SYSTEMCTL=${HUICE_SYSTEMCTL:-systemctl}
FORCE_ROOT=${HUICE_FORCE_ROOT:-0}
OBJ=.local-objects

START_SERVICE=1
FROM_BACKUP=0
BACKUP=
X=
OBJECT_STORE=local
#: idle → staged（旧树已在 stage）→ half-swapped（app 已挪走、stage 还没就位）→ swapped
STATE=idle
STOPPED_SERVICE=0
WORK=

usage() {
    printf '%s\n' \
        '用法：rollback.sh <X> [--base DIR] [--from-backup 包] [--object-store local|obs] [--no-service]' \
        '  X  §6.1 那次实际部署的提交；app.old-$X 里是更新前的代码树'
}

die() {
    local code=$1
    shift
    printf 'rollback.sh: %s\n' "$*" >&2
    exit "$code"
}

# --------------------------------------------------------------- 恢复动作


#: 把 stage 收回去，什么都不留。只在 app 还完好时调用。
discard_stage() {
    if [ ! -e "$STAGE" ]; then
        return 0
    fi
    if [ "$FROM_BACKUP" -eq 1 ]; then
        rm -rf -- "$STAGE"
    else
        mv -- "$STAGE" "$OLD"
    fi
}

#: 第二步 mv 失败后的收场：app 回到被替换下来的那份，stage 回到 app.old-$X。
#: 哪一步没成功都返回非零，由调用方把现场打出来——恢复失败不能被当成恢复成功。
restore_half_swap() {
    local failed=0
    if [ -e "$APP" ]; then
        printf 'rollback.sh: %s 已经存在，不再动它（人工确认）\n' "$APP" >&2
    else
        mv -- "$BAD" "$APP" || failed=1
    fi
    if [ -d "$STAGE" ]; then
        if [ -e "$OLD" ]; then
            printf 'rollback.sh: %s 已存在，stage 留在 %s（人工确认）\n' "$OLD" "$STAGE" >&2
            failed=1
        else
            mv -- "$STAGE" "$OLD" || failed=1
        fi
    fi
    return "$failed"
}

#: 恢复服务：只在「确实是我们停的」时候起，起不来要喊出来（返回非零）。
restart_service() {
    if [ "$STOPPED_SERVICE" -ne 1 ] || [ "$START_SERVICE" -ne 1 ]; then
        return 0
    fi
    if ! "$SYSTEMCTL" start "$SERVICE"; then
        printf 'rollback.sh: 原代码的服务没起来，需要人工处理：systemctl status %s\n' "$SERVICE" >&2
        return 1
    fi
}

#: 换目录之前失败：按当前阶段收回现场，服务按原代码重新起来，再以给定码退出。
abort() {
    local code=$1
    shift
    set +e
    printf 'rollback.sh: %s\n' "$*" >&2
    case $STATE in
        staged)
            discard_stage ||
                printf 'rollback.sh: 收回 %s 失败，现场留在那里，人工处理\n' "$STAGE" >&2
            ;;
        half-swapped)
            restore_half_swap ||
                printf 'rollback.sh: 现场没恢复完整：app=%s stage=%s bad=%s\n' \
                    "$APP" "$STAGE" "$BAD" >&2
            ;;
        *) ;;
    esac
    restart_service
    exit "$code"
}

#: ERR 兜底：没被显式分支接住的失败也不能把服务留在「已停止」。只报告，不掩盖。
on_error() {
    local line=$1
    set +e
    printf 'rollback.sh: 第 %s 行意外失败（阶段 %s）\n' "$line" "$STATE" >&2
    case $STATE in
        staged)
            discard_stage ||
                printf 'rollback.sh: 收回 %s 失败，人工处理\n' "$STAGE" >&2
            ;;
        half-swapped)
            restore_half_swap ||
                printf 'rollback.sh: 现场没恢复完整：app=%s stage=%s bad=%s\n' \
                    "$APP" "$STAGE" "$BAD" >&2
            ;;
        *) ;;
    esac
    restart_service
    exit 2
}

# ------------------------------------------------------------------ 对象


_sum() {
    # "$1" 一定是文件；读不了就让调用方决定怎么办（返回非零，不回显内容）。
    local out
    out=$(sha256sum -- "$1" 2>/dev/null) || return 1
    printf '%s' "${out%% *}"
}

#: 把目录里的文件列成 NUL 分隔清单。``find`` 读不全（权限、IO 错误）就返回非零——
#: 进程替换的退出状态传不出去，漏掉的对象会被当成「0 个对象」照样通过。
list_objects() {
    local dir=$1 out=$2
    (cd -- "$dir" && find . -type f -print0) >"$out"
}

#: 把 app/.local-objects 并进 stage。0 = 全部可并；3 = 同名不同内容/放不进去/清单读不全。
merge_objects() {
    local cur=$APP/$OBJ target=$STAGE/$OBJ rel src_sum dst_sum list=$WORK/objects.list
    local copied=0 same=0 conflicts=0 unreadable=0

    if ! list_objects "$cur" "$list"; then
        printf 'rollback.sh: 读不完 %s 里的对象清单（find 返回非零），没法判断是否完整\n' "$cur" >&2
        return 3
    fi
    mkdir -p -- "$target"
    while IFS= read -r -d '' rel; do
        rel=${rel#./}
        if [ -d "$target/$rel" ]; then
            printf 'rollback.sh: 目标里 %s 是目录、回滚前是文件——形状不一致\n' "$rel" >&2
            conflicts=$((conflicts + 1))
            continue
        fi
        if [ -e "$target/$rel" ]; then
            # 同名：内容一样就跳过，不一样必须停——cp -an 会静默保留旧的那份，
            # 而「路径相同」本身证明不了内容相同（M2 在 #44 的 P1）。
            if ! src_sum=$(_sum "$cur/$rel") || ! dst_sum=$(_sum "$target/$rel"); then
                printf 'rollback.sh: 读不了 %s 的内容，没法判断能不能覆盖\n' "$rel" >&2
                unreadable=$((unreadable + 1))
            elif [ "$src_sum" != "$dst_sum" ]; then
                printf 'rollback.sh: 同名对象但内容不同：%s\n' "$rel" >&2
                conflicts=$((conflicts + 1))
            else
                same=$((same + 1))
            fi
            continue
        fi
        mkdir -p -- "$(dirname -- "$target/$rel")"
        if ! cp -a -- "$cur/$rel" "$target/$rel"; then
            printf 'rollback.sh: 复制失败：%s\n' "$rel" >&2
            unreadable=$((unreadable + 1))
            continue
        fi
        copied=$((copied + 1))
    done <"$list"

    printf 'rollback.sh: 对象合并 复制 %d、同名相同 %d、同名不同 %d、读不了 %d\n' \
        "$copied" "$same" "$conflicts" "$unreadable" >&2
    if [ "$conflicts" -gt 0 ] || [ "$unreadable" -gt 0 ]; then
        return 3
    fi
}

#: 逐文件核对：回滚前的每个对象都要在 stage 里同名同内容。0 = 通过；4 = 不通过。
verify_objects() {
    local cur=$APP/$OBJ target=$STAGE/$OBJ rel src_sum dst_sum list=$WORK/objects.list
    local total=0 bad=0

    if ! list_objects "$cur" "$list"; then
        printf 'rollback.sh: 校验时读不完 %s 里的对象清单\n' "$cur" >&2
        return 4
    fi
    while IFS= read -r -d '' rel; do
        rel=${rel#./}
        total=$((total + 1))
        if [ ! -f "$target/$rel" ]; then
            printf 'rollback.sh: 合并后的树里缺 %s\n' "$rel" >&2
            bad=$((bad + 1))
            continue
        fi
        if ! src_sum=$(_sum "$cur/$rel") || ! dst_sum=$(_sum "$target/$rel"); then
            printf 'rollback.sh: 校验时读不了 %s\n' "$rel" >&2
            bad=$((bad + 1))
            continue
        fi
        if [ "$src_sum" != "$dst_sum" ]; then
            printf 'rollback.sh: 合并后内容不一致：%s\n' "$rel" >&2
            bad=$((bad + 1))
        fi
    done <"$list"

    printf 'rollback.sh: 校验 %d 个对象，有问题 %d 个\n' "$total" "$bad" >&2
    if [ "$bad" -gt 0 ]; then
        return 4
    fi
}

# ------------------------------------------------------------------ 参数

while [ $# -gt 0 ]; do
    case $1 in
        --base)
            [ $# -ge 2 ] || die 2 '--base 需要一个目录'
            BASE=$2
            shift 2
            ;;
        --from-backup)
            [ $# -ge 2 ] || die 2 '--from-backup 需要一个包路径'
            BACKUP=$2
            FROM_BACKUP=1
            shift 2
            ;;
        --object-store)
            [ $# -ge 2 ] || die 2 '--object-store 需要 local 或 obs'
            OBJECT_STORE=$2
            shift 2
            ;;
        --no-service)
            START_SERVICE=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            die 2 "不认识的参数：$1"
            ;;
        *)
            [ -z "$X" ] || die 2 '只接受一个提交号'
            X=$1
            shift
            ;;
    esac
done

[ -n "$X" ] || die 2 '缺少提交号（§6.1 部署的那个）'
case $OBJECT_STORE in
    local|obs) ;;
    *) die 2 "--object-store 只能是 local 或 obs（收到：$OBJECT_STORE）" ;;
esac

APP=$BASE/app
OLD=$BASE/app.old-$X
BAD=$BASE/app.bad-$X
STAGE=$BASE/app.stage-$X

[ -d "$APP" ] || die 2 "$APP 不存在：先看 §6.1 是不是做了一半"
[ ! -e "$BAD" ] || die 2 "$BAD 已存在（上一次回滚留下的）：先人工确认它是什么，别直接覆盖"
[ ! -e "$STAGE" ] || die 2 "$STAGE 已存在：上一次回滚没跑完？先人工处理"
if [ "$FROM_BACKUP" -eq 1 ]; then
    [ -f "$BACKUP" ] || die 2 "找不到备份包：$BACKUP"
else
    [ -d "$OLD" ] || die 2 "找不到 $OLD（没有整目录备份）：改用 --from-backup 指定备份包"
fi

# 对象存储模式必须与磁盘状态对得上，**在停服务之前**判：目录没了既可能是 obs，也可能是
# 部署事故，脚本不替操作员猜（M2 在 #44 的复审 P1）。
if [ "$OBJECT_STORE" = local ]; then
    [ -d "$APP/$OBJ" ] ||
        die 2 "local 模式下 $APP/$OBJ 不存在：先查是不是部署事故。若确实是 OBS 部署，用 --object-store obs 重跑"
else
    [ ! -e "$APP/$OBJ" ] ||
        die 2 "声明了 --object-store obs，但 $APP/$OBJ 存在：状态说不清，先人工确认"
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/huice-rollback.XXXXXX") || die 2 '建不了临时目录'
trap 'rm -rf -- "$WORK"' EXIT
trap 'on_error $LINENO' ERR
set -E

# ------------------------------------------------------------------ 执行

if [ "$START_SERVICE" -eq 1 ]; then
    "$SYSTEMCTL" stop "$SERVICE" ||
        die 2 "停不下 $SERVICE：先人工确认它现在的状态，再决定要不要继续"
    STOPPED_SERVICE=1
fi

# 旧树（或备份包）先落在 stage 上，app 在换目录之前一动不动。
# 每搞出一个 stage 就把 STATE 推到 staged：之后任何失败都能把它收回，不留半截目录。
if [ "$FROM_BACKUP" -eq 1 ]; then
    mkdir -p -- "$STAGE" || abort 2 "建不了 $STAGE"
    STATE=staged
    tar -xzf "$BACKUP" -C "$STAGE" --strip-components=1 ||
        abort 2 "解包失败：$BACKUP"
    [ -d "$STAGE/services/api" ] ||
        abort 2 "备份包里没有 services/api/：确认包内是 app/ 这一层（要 --strip-components=1）"
else
    mv -- "$OLD" "$STAGE" || abort 2 "把 $OLD 挪到 $STAGE 失败：什么都没换"
    STATE=staged
fi

if [ "$OBJECT_STORE" = local ]; then
    if ! merge_objects; then
        abort 3 '有同名对象内容不同（或读不了/放不进去）：没有换任何目录。先人工确认留哪份，处理完再跑'
    fi
    if ! verify_objects; then
        abort 4 '合并后校验不过：没有换任何目录，服务仍按原代码运行'
    fi
else
    printf 'rollback.sh: --object-store obs，回滚不涉及本地对象目录\n' >&2
fi

if [ "$(id -u)" = '0' ] || [ "$FORCE_ROOT" = '1' ]; then
    # 属主不存在、文件系统只读都会让这一步失败；此时 app 还没动，退回去是安全的。
    chown -R -- "$OWNER" "$STAGE" || abort 2 "改属主失败（$OWNER 是否存在？）：什么都没换"
fi
if [ -d "$STAGE/$OBJ" ]; then
    # unit 是 ProtectSystem=strict、只对这一个路径放行写入：模式不对，服务起来也写不进新对象。
    chmod 750 -- "$STAGE/$OBJ" 2>/dev/null ||
        printf 'rollback.sh: 改不了 %s 的权限，请人工确认它是 huice:huice 750\n' "$STAGE/$OBJ" >&2
fi

mv -- "$APP" "$BAD" || abort 2 "把当前 app 挪到 $BAD 失败：什么都没换"
STATE=half-swapped
if ! mv -- "$STAGE" "$APP"; then
    # 此时 app 不在原位、stage 还在：把两份都放回去，再起服务。恢复本身失败要照实说，
    # 不能让「恢复了」这句话盖过 app 仍缺席的事实（M2 在 #44 的复审 P1）。
    if restore_half_swap; then
        printf 'rollback.sh: 第二步切换失败，已把 app 与旧树放回原位\n' >&2
    else
        printf 'rollback.sh: 第二步切换失败，现场也没恢复完整：app=%s stage=%s bad=%s\n' \
            "$APP" "$STAGE" "$BAD" >&2
    fi
    STATE=idle # 恢复结果已经处理并报告，别再让下面重试一遍
    restart_service
    exit 2
fi
STATE=swapped

if [ "$START_SERVICE" -eq 1 ]; then
    if ! "$SYSTEMCTL" start "$SERVICE"; then
        die 5 "代码已换回，但 $SERVICE 没起来：journalctl -u $SERVICE。被替换掉的那份在 $BAD"
    fi
fi

printf 'rollback.sh: 已回滚到 %s 之前的代码树；%s 保留为现场，确认无误后再删\n' "$X" "$BAD"

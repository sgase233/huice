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
#   1) 先把旧树（或备份包）摆到 app.stage-$X，**不碰 app**；
#   2) 把 app/.local-objects 里的每个对象并进 stage：缺的复制，同名且内容相同的跳过，
#      同名而内容不同的**一律停下**（不覆盖，交给人决定留哪份）；
#   3) 逐文件比对 sha256 做校验：stage 里必须存在同名同内容的文件，缺一个就非零退出；
#   4) 以上都过了才换目录（app → app.bad-$X，stage → app），最后起服务。
# 在 4) 之前失败，app 还是原来那棵、服务在原代码上恢复；在 4) 之后服务起不来，手册
# 里写了怎么人工处理。
#
# 用法：rollback.sh <X> [--base DIR] [--from-backup 包] [--no-service]
#   X              §6.1 那次实际部署的提交（app.old-$X 里是它之前的版本）
#   --base DIR     部署根，默认 /opt/huice
#   --from-backup  改用更新前的整目录备份包还原（app.old-$X 已不可用时）
#   --no-service   不调用 systemctl（演练/测试用）
#
# 退出码：0 成功；2 前置条件不满足（什么都没动）；3 同名对象内容不同（什么都没换）；
#         4 合并后校验不过（什么都没换）；5 已换完但服务没起来。
#
# 仅供测试/特殊部署覆盖的变量（生产不需要设置）：
#   HUICE_BASE      部署根，默认 /opt/huice
#   HUICE_OWNER     app 的属主，默认 huice:huice（非 root 运行时不改属主）
#   HUICE_SERVICE   unit 名，默认 huice-api
#   HUICE_SYSTEMCTL systemctl 可执行文件
set -euo pipefail

BASE=${HUICE_BASE:-/opt/huice}
OWNER=${HUICE_OWNER:-huice:huice}
SERVICE=${HUICE_SERVICE:-huice-api}
SYSTEMCTL=${HUICE_SYSTEMCTL:-systemctl}
OBJ=.local-objects

START_SERVICE=1
FROM_BACKUP=0
BACKUP=
X=

usage() {
    printf '%s\n' \
        '用法：rollback.sh <X> [--base DIR] [--from-backup 包] [--no-service]' \
        '  X  §6.1 那次实际部署的提交；app.old-$X 里是更新前的代码树'
}

die() {
    local code=$1
    shift
    printf 'rollback.sh: %s\n' "$*" >&2
    exit "$code"
}

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

#: 把 stage 收回去，什么都不留。只有在换目录之前调用。
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

#: 换目录之前失败：收掉 stage，把服务按原来的代码重新起来，再以给定码退出。
abort() {
    local code=$1
    shift
    printf 'rollback.sh: %s\n' "$*" >&2
    discard_stage
    if [ "$START_SERVICE" -eq 1 ]; then
        "$SYSTEMCTL" start "$SERVICE" ||
            printf 'rollback.sh: 原代码的服务没起来，人工处理：systemctl status %s\n' "$SERVICE" >&2
    fi
    exit "$code"
}

_sum() {
    # "$1" 一定是文件；读不了就让调用方决定怎么办（返回非零，不回显内容）。
    local out
    out=$(sha256sum -- "$1" 2>/dev/null) || return 1
    printf '%s' "${out%% *}"
}

#: 把 app/.local-objects 并进 stage。0 = 全部可并；3 = 有同名不同内容，什么都没换。
merge_objects() {
    local cur=$APP/$OBJ target=$STAGE/$OBJ rel src_sum dst_sum
    local copied=0 same=0 conflicts=0 unreadable=0

    if [ ! -d "$cur" ]; then
        printf 'rollback.sh: 没有 %s（obs 模式），回滚不涉及本地对象目录\n' "$cur" >&2
        return 0
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
    done < <(cd -- "$cur" && find . -type f -print0)

    printf 'rollback.sh: 对象合并 复制 %d、同名相同 %d、同名不同 %d、读不了 %d\n' \
        "$copied" "$same" "$conflicts" "$unreadable" >&2
    if [ "$conflicts" -gt 0 ] || [ "$unreadable" -gt 0 ]; then
        return 3
    fi
}

#: 逐文件核对：回滚前的每个对象都要在 stage 里同名同内容。0 = 通过；4 = 不通过。
verify_objects() {
    local cur=$APP/$OBJ target=$STAGE/$OBJ rel src_sum dst_sum
    local total=0 bad=0

    if [ ! -d "$cur" ]; then
        return 0
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
    done < <(cd -- "$cur" && find . -type f -print0)

    printf 'rollback.sh: 校验 %d 个对象，有问题 %d 个\n' "$total" "$bad" >&2
    if [ "$bad" -gt 0 ]; then
        return 4
    fi
}

if [ "$START_SERVICE" -eq 1 ]; then
    "$SYSTEMCTL" stop "$SERVICE" || die 2 "停不下 $SERVICE：先人工确认它现在的状态"
fi

# 旧树（或备份包）先落在 stage 上，app 在换目录之前一动不动。
if [ "$FROM_BACKUP" -eq 1 ]; then
    mkdir -p -- "$STAGE"
    if ! tar -xzf "$BACKUP" -C "$STAGE" --strip-components=1; then
        abort 2 "解包失败：$BACKUP"
    fi
    if [ ! -d "$STAGE/services/api" ]; then
        abort 2 "备份包里没有 services/api/：确认包内是 app/ 这一层（要 --strip-components=1）"
    fi
else
    mv -- "$OLD" "$STAGE"
fi

if ! merge_objects; then
    abort 3 '有同名对象内容不同（或读不了）：没有换任何目录。先人工确认留哪份，处理完再跑'
fi
if ! verify_objects; then
    abort 4 '合并后校验不过：没有换任何目录，服务仍按原代码运行'
fi

if [ "$(id -u)" = '0' ]; then
    chown -R -- "$OWNER" "$STAGE"
fi
if [ -d "$STAGE/$OBJ" ]; then
    # unit 是 ProtectSystem=strict、只对这一个路径放行写入：模式不对，服务起来也写不进新对象。
    chmod 750 -- "$STAGE/$OBJ" 2>/dev/null ||
        printf 'rollback.sh: 改不了 %s 的权限，请人工确认它是 huice:huice 750\n' "$STAGE/$OBJ" >&2
fi

mv -- "$APP" "$BAD"
mv -- "$STAGE" "$APP"

if [ "$START_SERVICE" -eq 1 ]; then
    if ! "$SYSTEMCTL" start "$SERVICE"; then
        die 5 "代码已换回，但 $SERVICE 没起来：journalctl -u $SERVICE。被替换掉的那份在 $BAD"
    fi
fi

printf 'rollback.sh: 已回滚到 %s 之前的代码树；%s 保留为现场，确认无误后再删\n' "$X" "$BAD"

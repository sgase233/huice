#!/usr/bin/env python3
"""按 systemd ``EnvironmentFile`` 的规则读取单个变量，**不执行**文件内容。

为什么不用 ``. /etc/huice/api.env``：shell 会按 shell 语法解析**整个文件**——口令或
密钥里出现 ``$``、反引号、``!``、未配对引号时，轻则读到的值与原值不同，重则执行
文件里的内容。systemd 的 ``EnvironmentFile`` 不是 shell：不做变量展开、不执行命令，
只处理引号与反斜杠。§2.1 的迁移命令要的是「systemd 实际会传给服务进程的那个值」，
所以按 systemd 的规则读，而不是按 shell 的规则 source。

支持的写法（与 systemd 一致的部分）：

* 每行 ``NAME=VALUE``；行首空白忽略；``#`` 或 ``;`` 开头的整行是注释。
* 不加引号：值原样保留，去掉行尾空白与换行。
* 单引号 ``'...'``：内部一律字面量。
* 双引号 ``"..."``：只处理 ``\\\\`` ``\\"`` ``\\'`` ``\\$`` ``\\``` 五个转义，其余反斜杠原样保留。
* 不做任何展开：``$VAR``、``$(...)``、反引号在三种写法里都只是普通字符。

用法（只输出值，不带结尾换行，便于 ``$(...)`` 取值）：

    DATABASE_URL=$(python3 /opt/huice/app/infra/deploy/read-env.py /etc/huice/api.env DATABASE_URL)

出错时只报变量名与文件路径，**不回显值**；找不到变量、或变量值为空时退出码 2。
"""

from __future__ import annotations

import sys
from pathlib import Path

DOUBLE_QUOTE_ESCAPES = {'\\': '\\', '"': '"', "'": "'", "$": "$", "`": "`"}


def parse_environ_file(text: str) -> dict[str, str]:
    """把 ``KEY=VALUE`` 文本解析成字典；同名键以后出现的为准（与 systemd 一致）。"""

    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.lstrip()
        if not line or line[0] in "#;":
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name:
            continue
        values[name] = _unquote(value)
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return _unescape(value[1:-1])
    return value.rstrip()


def _unescape(value: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            following = value[index + 1]
            replacement = DOUBLE_QUOTE_ESCAPES.get(following)
            if replacement is None:
                # 未知转义原样保留：宁可多一个反斜杠，也不要悄悄改掉口令。
                out.append(char)
                out.append(following)
            else:
                out.append(replacement)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法：read-env.py <EnvironmentFile> <变量名>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    name = argv[2]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"读不到环境文件 {path}：{exc.strerror}", file=sys.stderr)
        return 2
    values = parse_environ_file(text)
    if name not in values:
        print(f"环境文件 {path} 里没有变量 {name}。", file=sys.stderr)
        return 2
    if not values[name]:
        # 空值比缺变量更危险：psql 拿到空的连接串会退回环境变量/默认值，可能连到别的库。
        # 手册里的调用方都不需要「空」，一律按「取不到」处理。
        print(f"环境文件 {path} 里变量 {name} 是空值。", file=sys.stderr)
        return 2
    # 值本身也是凭据：只写到 stdout，由调用方决定去哪儿；不打印任何提示。
    sys.stdout.write(values[name])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

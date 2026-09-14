#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bulk-write-guard —— ② 批量改写命令门（`PreToolUse` · 挂 `Bash`）

拦什么：**一条命令能改掉许多文件、而你事先没看过清单** 的写法。
不拦什么：单文件编辑（那是 `Edit` 工具，不走本门）；只读的批量命令（`grep -r` 等）。

判据（**不是"看起来危险"，是"改动面超出你能一眼看完的范围"**）：

| 形状 | 为什么拦 |
|---|---|
| `find … -exec …` | 改动面由 `find` 决定 —— **你写命令时不知道它会命中几个** |
| `xargs` + `rm/mv/cp/sed/sh` | 同上，且目标来自管道 |
| `for … do … done` 里带改写 | 循环体里的目标通常是变量，**看不见实际范围** |
| `sed -i` / `perl -i` + 通配 | 就地改写 + 通配 = 面不可见 |
| `rm` / `mv` + 通配 | 同上 |

⚠️ **本门的真实价值不是"拦住"，是"让你先看清单"。**
   触发后正确的做法**不是换个写法绕过去**，是：
   **先跑一遍不带改动的版本，把命中的文件列出来看一眼。**

⚠️ 定位：**防手滑，不防对抗**。已知绕过：把命令写进脚本再执行、`$(...)`、变量拼接。

⚠️ 子 Agent：**只注入 `additionalContext`，不 ask**（子 Agent 无人可批准，ask 会挂死）。

自测：python3 bulk-write-guard.py --self-test
"""

import json
import re
import sys

RULES = [
    ("find + -exec", re.compile(r"\bfind\b[^\n|;&]*-exec\b"),
     "改动面由 `find` 决定 —— 写命令时你不知道它会命中几个文件。"),
    ("xargs 批处理", re.compile(r"\bxargs\b[^\n;&|]*\b(?:rm|mv|cp|sed|perl|sh|bash)\b"),
     "目标来自管道，范围不可见。"),
    ("循环里改写", re.compile(r"\bfor\b[\s\S]{0,200}?\bdo\b[\s\S]{0,400}?\b(?:sed|perl|rm|mv)\b"),
     "循环体里的目标通常是变量，实际范围看不见。"),
    ("就地改写 + 通配", re.compile(r"\b(?:sed|perl)\b[^\n;&|]*-i[^\n;&|]*[*?]"),
     "`-i` 是就地改（不可逆），配通配符则面不可见。"),
    ("通配批量删/移", re.compile(r"\b(?:rm|mv)\b[^\n;&|]*[*?]"),
     "通配 + 删/移 = 面不可见，且删了拿不回来。"),
]


# ⚠️ 匹配前先去掉引号内内容 —— 否则 `echo "find . -exec rm {} ;"` 这类
#    **只是提到**批量写法的命令也会被拦（同 `outward-guard` 的实测误报，2026-09-15）。
RE_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")


def find_hits(payload):
    if payload.get("tool_name") != "Bash":
        return []
    raw = (payload.get("tool_input") or {}).get("command", "") or ""
    cmd = RE_QUOTED.sub(" ", raw)
    for name, pat, why in RULES:
        if pat.search(cmd):
            return [(name, why)]
    return []


def emit(reason, agent_type):
    """**故意不对称**：主会话 ask；子 Agent 只注入提示。"""
    if agent_type:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": reason}},
            ensure_ascii=False))
    else:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
            "additionalContext": reason}}, ensure_ascii=False))


def main():
    if "--self-test" in sys.argv:
        return self_test()
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        emit(f"bulk-write-guard 无法解析钩子输入（{e}），请人工确认", None)
        return 0
    hits = find_hits(payload)
    if not hits:
        return 0
    body = "\n".join(f"  · [{n}] {w}" for n, w in hits)
    emit("⛔ bulk-write-guard：这条命令的**改动面不可见**，需要人工确认。\n\n"
         f"{body}\n\n"
         "**正确做法不是换个写法绕过去** —— 是先跑一遍**不带改动**的版本，"
         "把命中的文件列出来看一眼，确认范围后再改。",
         (payload.get("agent_type") or "").strip())
    return 0


# ══════════════════════════════════════════════════════════════════
def self_test():
    cases = [
        # 应拦
        ("find -exec sed",   r"find . -name '*.md' -exec sed -i '' 's/a/b/' {} \;", 1),
        ("find -exec rm",    r"find . -name '*.tmp' -exec rm {} \;",                 1),
        ("xargs + rm",       r"ls | xargs rm",                                       1),
        ("for 循环改",        r"for f in *.md; do sed -i '' 's/x/y/' $f; done",      1),
        ("sed -i + 通配",     r"sed -i '' 's/x/y/' *.md",                            1),
        ("rm + 通配",         r"rm -f build/*",                                      1),
        ("mv + 通配",         r"mv src/*.js dist/",                                  1),
        # 应放
        ("单文件 sed -i",     r"sed -i '' 's/a/b/' one.md",                          0),
        ("只读 find",         r"find . -name '*.md'",                                0),
        ("只读 grep -r",      r"grep -rn 'x' .",                                     0),
        ("rm 单文件",         r"rm notes.txt",                                       0),
        ("xargs 只读",        r"ls | xargs echo",                                    0),
        ("普通 for 不改写",    r"for f in *.md; do echo $f; done",                    0),
        # ⚠️ 实测误报逼出来的：只是**提到**批量写法的命令，不该拦
        ("只是提到 · echo",    r'echo "find . -exec rm {} ;"',                       0),
        ("只是提到 · 写文档",   r'printf "批量: sed -i \x27s/a/b/\x27 *.md\n"',       0),
    ]
    print("bulk-write-guard · 突变验证\n")
    ok = fail = 0
    for desc, cmd, expect in cases:
        got = 1 if find_hits({"tool_name": "Bash", "tool_input": {"command": cmd}}) else 0
        good = got == expect
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print(f"  {'✅' if good else '❌'} {desc:<20} 期望={'拦' if expect else '放'}  实际={'拦' if got else '放'}")

    # 反证：非 Bash 工具必须**永远不命中**
    got = 1 if find_hits({"tool_name": "Edit", "tool_input": {"command": "find . -exec rm {} ;"}}) else 0
    good = got == 0
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"  {'✅' if good else '❌'} {'反证 · 非 Bash 工具不命中':<20} 期望=放  实际={'拦' if got else '放'}")

    print(f"\n  通过 {ok} · 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

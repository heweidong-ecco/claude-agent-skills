#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dump-user-msgs —— 从会话记录里抽出**用户说过的话**（逐字）

为什么需要它
------------
「价值对话」「节点对话过程内容」这些东西**只活在会话记录里** ——
`~/.claude/projects/<项目>/<会话>.jsonl`。**文档里没有，仓里也没有。**

本脚本把它们抽出来，好让人能引用**原话**，而不是二手转述。

⚠️ 三个坑（**都踩过**，脚本里都处理了）
---------------------------------------

**① 样板文字会被当成"用户发言"。**
调 skill 时那条 `Base directory for this skill: …` **有三万字**，
它是以 `type: "user"` 记的，不滤的话**会把真发言整个淹掉**。
（实测：不滤时最长的一条 33,274 字，全是样板。）

**② 同一条消息会出现多次**（重试 / 回显）—— 必须去重。

**③ 一条会话里可能混着多个项目。**
本次会话里就混着**求职**和**这个 skill** 两个项目的对话。
**直接整篇搬，会把另一个项目的私事搬进公开仓。**
⇒ 脚本给一个 `--classify` 开关，把命中的和没命中的**分开输出**，**由人来判**。

⛔ **它只抽，不判。**
"哪条属于哪个项目"是要人看的 —— 脚本给的是候选，不是分类结果。

用法
----
    # 只抽
    python3 dump-user-msgs.py --session <会话.jsonl> --out <留档目录>

    # 抽 + 按关键词分堆（**候选，仍需人判**）
    python3 dump-user-msgs.py --session <会话.jsonl> --out <目录> \
        --classify 'grilling|skill|harness|触发|记忆'
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# ⚠️ 这些开头的**都不是**用户说的话（见文件头坑 ①）
NOISE = (
    "Base directory for this skill:",
    "Another Claude session sent a message:",
    "## Context Usage",
    "This session is being continued",
    "<command-name>",
    "<local-command",
    "Caveat:",
    "<system-reminder>",
)


def extract(session: Path):
    """返回 [(时间戳, 原文)] —— 逐字，已去重、已滤样板。"""
    msgs, seen = [], set()
    for line in session.open(encoding="utf-8", errors="ignore"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "user":
            continue
        m = d.get("message") or {}
        c = m.get("content")
        if isinstance(c, str):
            s = c
        elif isinstance(c, list):
            # 带 tool_result 的整条跳过 —— 那是工具输出，不是人说的
            if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                continue
            s = " ".join(x.get("text", "") for x in c
                         if isinstance(x, dict) and x.get("type") == "text")
        else:
            continue
        s = (s or "").strip()
        if not s or s.startswith("<"):
            continue
        if any(s.startswith(n) or n in s[:120] for n in NOISE):
            continue
        h = hashlib.sha1(s.encode()).hexdigest()
        if h in seen:                       # 坑 ②：去重
            continue
        seen.add(h)
        msgs.append((d.get("timestamp", ""), s))
    return msgs


def render(title, msgs):
    out = [f"# {title}", "", f"> 共 **{len(msgs)}** 条 · 逐字 · 已去重 / 已滤样板 / 已剔除工具结果", ""]
    for i, (ts, s) in enumerate(msgs, 1):
        out += [f"## [{i}] {str(ts)[:19]}", "", s, ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="会话 .jsonl 路径")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--classify", default=None,
                    help="正则。命中的进 `_命中.md`，其余进 `_其余.md`。"
                         "**只分堆，不下结论** —— 哪条属于哪个项目要人判。")
    args = ap.parse_args()

    session = Path(args.session)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    msgs = extract(session)
    total = sum(len(s) for _, s in msgs)
    print(f"抽出 {len(msgs)} 条 / {total} 字")
    if not msgs:
        print("⛔ 一条都没有 —— 先确认这个会话里真的有用户发言。")
        return 1

    if args.classify:
        pat = re.compile(args.classify)
        hit = [x for x in msgs if pat.search(x[1])]
        rest = [x for x in msgs if not pat.search(x[1])]
        (out / "_命中.md").write_text(render("命中 --classify 的（候选）", hit), encoding="utf-8")
        (out / "_其余.md").write_text(render("未命中的（候选）", rest), encoding="utf-8")
        print(f"  命中 {len(hit)} 条 → _命中.md")
        print(f"  其余 {len(rest)} 条 → _其余.md")
        print("⚠️ 这只是**分堆**：关键词会漏也会多捞。**哪条属于哪个项目，要人看。**")
    else:
        (out / "用户发言全集.md").write_text(render("用户发言全集", msgs), encoding="utf-8")
        print(f"→ {out/'用户发言全集.md'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

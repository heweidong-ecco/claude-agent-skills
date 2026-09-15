#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stop-guard —— ④ Stop 门（回答收尾时跑）

# ══════════════════════════════════════════════════════════════════
# ⛔ 未启用 · 候选 —— **不要注册进 settings.json**
# ══════════════════════════════════════════════════════════════════
#
# 实测否定了它（2026-09-15，在某会话的 **241 条**真实 assistant 回答上跑）：
#
#   命中 9 条（3.7%）—— **9 条全是误报，真阳 0 条**。
#
#   逐条看触发源：
#     · `等你` × 7 —— 「等你确认」「等你点头」「等你回那 5 个问题」「等你写」
#                      **全是"把球踢回去"，正是本门想鼓励的行为**
#     · `先停` × 2 —— 「出来先停下给你过目，行吗？」（在征求同意）
#                      以及**讨论本门时引用的一句坏回答**
#
#   根因：**触发词抓的是「停」这个动作，不是「没说缺什么」。**
#         而实践中，说"停/等你"时**几乎总是同时说了要什么** ——
#         豁免是常态、不是例外。收窄到"通篇无具体索取"也不行：
#         **豁免词表永远列不全，而机器判不了"具体"。**
#
#   241 条里，「停但不说缺什么」这个形状**一次都没出现**。
#
# ⇒ 结论（**比这道门本身有用**）：
#     这是能找到的、**最接近"意图"的代理指标** —— 它在真实语料上 100% 误报。
#     **「意图层」可能连它的代理都拦不住。**
#     意图那一层，交给 `SKILL.md`（L1/L2），**不要指望 hook**。
#
# 保留本文件是为了**留下这条有证据的"行不通"记录**，不是留一道门。
# ══════════════════════════════════════════════════════════════════

**这是本模版里唯一试过触碰「意图」那一层的一道门** —— 它不判"意图对不对"（判不了），
只判**"该说的有没有说"**。**实测证明这条路走不通，见上。**

拦什么（两条，都来自真实事故）：

| 形状 | 为什么拦 |
|---|---|
| **宣布"停止 / 等你"却没说缺什么** | 用户会反问「**为什么要停止不动**」。停不是问题，**不说缺什么才是** —— 球必须踢回去 |
| **第一人称推定，却整段没给依据** | 这就是"补全了理解就往下走"，而且**不告诉你补了什么** |

**不拦什么**：结论正确与否、方案好不好、意图对不对 —— **都判不了**。

⚠️ 定位：**防手滑，不防对抗**；且**误报率未经过大规模验证** ——
   它是**候选门**，先跑一段看噪音能不能忍。忍不了就调判据，别整道拆掉。

⚠️ 防无限循环：`stop_hook_active` 为真时**一律放行**（已在 block 后的续跑中）。

自测：python3 stop-guard.py --self-test
"""

import json
import os
import re
import sys

# ⚠️ 门槛别设太高：一条**真实的坏回答**可能只有几十字
#    （例：「我先停在这里不动下一步，等你的指示。」= 46 字）。
#    30 是"纯应答"（好/收到/明白）与"有实质内容"之间的分界。
MIN_LEN = 30
TAIL_BYTES = 512 * 1024

# (名称, 触发, 豁免, 说明)
#   命中条件 = 触发命中 **且** 豁免在全文都没命中
RULES = [
    ("宣布停止但没说缺什么",
     # ⚠️ 触发词要**够宽**，否则「我先停在这里…」这种最典型的形状会漏掉
     #    —— 本条的初版就漏了，且自测还"通过"了（假通过）。
     re.compile(r"停止不动|先停|停下来|暂停|等你|等您|不动下一步|不再往下|先不执行|先不做"),
     # 豁免 = 「说清了缺什么」。注意 `等你决定` 属**索取**（要一个决定），
     # 不属于"停止不说缺什么"，必须放行 —— 否则会对正常收尾误报。
     re.compile(r"需要你|请您|请提供|我需要|要你确认|希望你|请你|要我"
                r"|等你决定|由你决定|由你定|要不要|你定"),
     "停不是问题 —— **不说缺什么**才是。用户会反问「为什么要停止不动」。\n"
     "    正确做法：把球踢回去 —— 「要继续，我需要你补充 X（为什么需要它），拿到后我做 Y」。"),

    ("第一人称推定，但没给依据",
     re.compile(r"我判断|我以为|我猜|我推测|我理解你的意思|我理解你要|大概是想|应该是想"),
     re.compile(r"[A-Za-z0-9_/.\-]+\.(?:md|py|json|sh|txt|csv|ya?ml)"
                r"|行\s*\d+|Code/|「|https?://|依据|来源|引文|逐字"),
     "这就是「**补全了理解就往下走**」，而且不告诉你补了什么。\n"
     "    正确做法：摊开 —— 「我理解你要 X（依据：<哪句/哪个文件>），对吗？」"),
]


# ══════════════════════════════════════════════════════════════════
def last_assistant_text(transcript, max_bytes=TAIL_BYTES):
    """**从尾部倒着读** —— transcript 可能几十 MB，不能全读。"""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read().decode("utf-8", "ignore")
    except OSError:
        return ""
    lines = data.split("\n")
    if start > 0:
        lines = lines[1:]                     # 丢掉被截断的首行
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        blocks = (d.get("message") or {}).get("content")
        if not isinstance(blocks, list):
            continue
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return ""


def find_hits(text):
    hits = []
    if len(text) < MIN_LEN:
        return hits
    for name, trig, exempt, why in RULES:
        if trig.search(text) and not exempt.search(text):
            hits.append((name, why))
    return hits


def main():
    if "--self-test" in sys.argv:
        return self_test()

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                              # 解不开就放行（Stop 门不 fail-closed，
                                              # 否则会把一次解析失败变成"卡住不让停"）
    # 防无限循环
    if payload.get("stop_hook_active"):
        return 0

    text = last_assistant_text(payload.get("transcript_path") or "")
    hits = find_hits(text)
    if not hits:
        return 0

    body = "\n".join(f"  · [{n}] {w}" for n, w in hits)
    reason = ("⛔ stop-guard：**有一件该说的没说**。\n\n"
              f"{body}\n\n"
              "这是提示，不是禁令 —— 若确实不适用，说明理由后即可结束。")
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


# ══════════════════════════════════════════════════════════════════
def self_test():
    cases = [
        # ① 停止类
        ("停 + 没说缺什么（应拦）",
         "我先停在这里不动下一步，等你的指示。后面还有很多事要做，"
         "但我觉得现在不适合继续，所以先停下来。", 1),
        ("停 + 说了缺什么（应放）",
         "我先停在这里。要继续，我需要你补充 Product 里各项目的形态"
         "（哪些是活的、哪些是蓝图），拿到后我再做映射。", 0),
        # ② 推定类
        ("推定 + 无依据（应拦）",
         "我理解你的意思了，我应该把这个仓库的结构改成两层，因为这样更清楚，"
         "所以我直接改了，改完之后会更好维护。", 1),
        ("推定 + 有依据（应放）",
         "我理解你要的是「本机目录与仓库同名」（依据：你在 2026-09-15 说过"
         "这两个搬过来的仓库要和 GitHub 仓库修改成同名），所以我改名了。", 0),
        # 普通回答
        ("普通长回答（应放）",
         "这次改动落在三个文件上：README 重写、卫生门规则修正、harness 新增。"
         "测试结果是 50 项全过。下一步等你决定要不要提交。", 0),
        ("纯应答 · 太短（应放）",
         "好。", 0),
    ]
    print("stop-guard · 突变验证\n")
    ok = fail = 0
    for desc, text, expect in cases:
        got = 1 if find_hits(text) else 0
        good = got == expect
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print(f"  {'✅' if good else '❌'} {desc:<30} 期望={'拦' if expect else '放'}  实际={'拦' if got else '放'}")

    # 反证：把豁免拿掉 —— "停+说了缺什么"必须**反而被拦**（证明豁免真的在起作用）
    # ⚠️ 这段文本**必须真的命中触发词** —— 否则测的是"没触发"，不是"豁免生效"
    #    （本条的初版就栽在这：文本没触发，反证"通过"了，实际什么都没测到）。
    saved = RULES[0][2]
    RULES[0] = (RULES[0][0], RULES[0][1],
                re.compile(r"$^"), RULES[0][3])       # 永不命中的豁免
    probe = ("我先停在这里。要继续，我需要你补充 Product 各项目的形态，"
             "拿到后我再做映射，这样才不会再走偏。")
    assert RULES[0][1].search(probe), "反证样本没命中触发词 —— 这个反证是假的"
    got = 1 if find_hits(probe) else 0
    RULES[0] = (RULES[0][0], RULES[0][1], saved, RULES[0][3])
    good = got == 1
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"  {'✅' if good else '❌'} {'反证 · 拿掉豁免后应反过来命中':<30} 期望=拦  实际={'拦' if got else '放'}")

    print(f"\n  通过 {ok} · 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

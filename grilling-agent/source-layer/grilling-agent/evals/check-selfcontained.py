#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check-selfcontained —— 扫描评测集里的「指代悬空」候选

它为什么存在
------------
2026-09-15 实测：**四条反例误触发，根因是「指代悬空」**——
「把我**刚才说的那句话**里的「很」改成「非常」」，
而 `claude -p` 是**无状态**的，**没有"刚才"**。

当时是用**手写正则**扫的，而且**扫漏了一条**：
正则有 `这个(?:函数|截图|项目|报错)`，**没有 `这段`**，
于是「把**这段** JSON 格式化一下」逃过一劫，在下一轮跑出 2/5 误触发。

⇒ **手写正则漏了；漏掉的那条，恰好是唯一还出错的那条。**
所以这次把判据写成脚本，而不是再手写一次。

判据（一句话）
--------------
> **反例必须自足，正例不必。**
> 反例的定义是「简单、一眼能核」——**连操作对象都不存在，它就不简单了**。
> 正例是在**描述一类任务**，形状本身就是信号。

所以本脚本对**正例和反例分别报告**：
    · 反例里的悬空候选 → ⛔ **必须处理**（判据直接要求）
    · 正例里的悬空候选 → ℹ️ 可容忍，但值得知道

⛔ 它只出**候选**，不出判决
--------------------------
"这句话是不是自足"**没有纯机械的判法**。
本脚本只能答一个粗问题：**有没有指代词，且没给内联载荷**。
**最终必须人看一眼。** 把它当裁判用，就是又给了一次"尺子坏了自己不知道"的机会。

**两个方向都会错（实测，不是假设）**：

| 方向 | 实例 | 为什么错 |
|---|---|---|
| **假阴性** | 「把我刚才说的那句话里的「很」改成「非常」」 | 它认为"有载荷"—— 可那个载荷「非常」是**替换内容**，不是**被改的对象**。对象**仍然不存在** |
| **假阳性** | 「这个 TypeScript 的报错 Property 'x' does not exist…」 | 它认为"没载荷"—— 可**报错原文就写在句子里**，它是自足的 |

⇒ **所以本脚本的断言只覆盖"指代词检测"（机械），
  绝不覆盖"是否自足"（判断）。** 把两者混在一个断言里，
  就会得到一个**"测不了、却看着像测了"的自检** ——
  和本仓踩过的那次"假通过"是同一类错。

用法
----
    python3 check-selfcontained.py                       # 扫默认评测集
    python3 check-selfcontained.py --eval-set <路径>
    python3 check-selfcontained.py --self-test           # ⭐ 先证明它能被证伪
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SET = HERE / "触发查询.json"

# ── 指代词 ────────────────────────────────────────────────────────────
# ⚠️ 这个表**只能靠踩坑加**。`这段` 就是这么补进来的。
#    加新词的时候，顺手在 --self-test 里加一条正样本。
REFERENTS = [
    ("这段",  r"这段"), ("这个", r"这个"), ("这份", r"这份"),
    ("这句",  r"这句"), ("这张", r"这张"), ("这笔", r"这笔"),
    ("这几",  r"这几"), ("那几", r"那几"), ("那个", r"那个"),
    ("那份",  r"那份"), ("那张", r"那张"), ("那些", r"那些"),
    ("刚才",  r"刚才"), ("刚刚", r"刚刚"),
    ("上次",  r"上次"), ("上个月", r"上个月"), ("之前那", r"之前那"),
]

# ── 内联载荷：出现这些，就当作"对象已给出" ──────────────────────────
# ⚠️ 这条是**粗的**：它只认"有没有引号/代码块"，
#    判不了"用文字描述清楚算不算给出"。**所以它只能出候选。**
PAYLOAD = [
    re.compile(r"[「『][^」』]{2,}[」』]"),          # 「…」
    re.compile(r"[“”][^“”]{2,}[“”]"),  # “…”
    re.compile(r"`[^`]{2,}`"),                     # `…`
    re.compile(r"[:：]\s*\S{4,}"),                  # 冒号后面的内容
    re.compile(r"\{.*\}"),                          # {…} 一段结构
    re.compile(r"[A-Za-z_]+Error|EADDRINUSE|Traceback", re.I),  # 报错原文
]


def survey(query):
    """返回 (命中的指代词列表, 是否有内联载荷)。"""
    hits = [name for name, pat in REFERENTS if re.search(pat, query)]
    has_payload = any(p.search(query) for p in PAYLOAD)
    return hits, has_payload


def scan(path):
    """按「带不带指代词」分堆 —— **不做自足判决**（判不了，见文件头）。"""
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    ref_neg, ref_pos = [], []
    for i, x in enumerate(items, 1):
        q, want = x.get("query", ""), bool(x.get("should_trigger"))
        hits, payload = survey(q)
        if not hits:
            continue
        (ref_pos if want else ref_neg).append((i, hits, payload, q))
    return items, ref_neg, ref_pos


def report(items, ref_neg, ref_pos):
    n_neg = sum(1 for x in items if not x.get("should_trigger"))
    print(f"扫描评测集：{len(items)} 条（该触发 {len(items)-n_neg} / 不该 {n_neg}）")
    print("─" * 76)

    print(f"\n⛔ 反例（**必须自足**）—— 带指代词的逐条列出来给人看：")
    if ref_neg:
        for i, hits, payload, q in ref_neg:
            print(f"   ▶ #{i:2d}  指代={hits}  内联载荷={'有' if payload else '无'}")
            print(f"          {q}")
    rest = n_neg - len(ref_neg)
    if rest:
        print(f"   其余 {rest} 条：**无指代词** ✅ 自足性风险低")

    print(f"\nℹ️  正例（**允许悬空**，仅供参考）：{len(ref_pos)} 条")
    for i, hits, payload, q in ref_pos:
        print(f"   · #{i:2d}  指代={hits}  内联载荷={'有' if payload else '无'}  {q[:40]}")

    print("\n" + "─" * 76)
    print("⚠️ 这是**候选**，不是判决。本脚本两个方向都会错（见文件头实例）——")
    print("   **判决必须人做**，尤其是「载荷是不是那个对象」这一层。")
    return len(ref_neg)


def self_test():
    """⭐ 只断言**机械**的部分：指代词检测会不会漏。

    ⛔ **不断言"是否自足"** —— 那是判断，判不了（见文件头两个方向都错的实例）。
    ⛔ **不把两者混在一个断言里** —— 混了就会得到一个"测不了、却看着像测了"的自检，
       和本仓踩过的那次"假通过"是同一类错。
    """
    print("自检①：指代词检测**必须**命中这些（漏一个 = 又漏一条悬空）")
    MUST_FIRE = [
        ("把这段 JSON 格式化一下，现在是一行看着难受", "踩坑原型：当时手写正则漏了「这段」"),
        ("把我刚才说的那句话里的「很」改成「非常」", "时间指代"),
        ("这个函数返回的是 Promise 还是 Promise<T>，我不太确定", "指示代词"),
        ("你看这个截图里报的是端口被占用了对吧", "指示代词"),
    ]
    for q, why in MUST_FIRE:
        hits, _ = survey(q)
        # ⚠️ 这行 assert 是关键：先证明样本**真的能命中**，否则这个自检是假的
        assert hits, f"漏了：{q}（{why}）"
        print(f"   ✅  指代={hits}  {q[:38]}   ({why})")

    print("\n自检②：**必须不**命中的（没有指代词）")
    NO_FIRE = ["现在几点", "帮我跑一下 pytest 看看现在过不过",
               "在桌面新建一个文件夹，名字叫 2026Q1-项目归档"]
    for q in NO_FIRE:
        hits, _ = survey(q)
        assert not hits, f"误报：{q} → {hits}"
        print(f"   ✅  无指代  {q[:46]}")

    print("\n自检③：**明知会错**的两条，写死在这里，防止有人以为它能当裁判")
    KNOWN_WRONG = [
        ("把我刚才说的那句话里的「很」改成「非常」", "假阴性：把「非常」（替换内容）当成了载荷"),
        ("这个 TypeScript 的报错 Property 'x' does not exist on type 'Y' 是什么意思",
         "假阳性：判成无载荷，其实报错原文就在句子里"),
    ]
    for q, why in KNOWN_WRONG:
        hits, payload = survey(q)
        print(f"   ⚠️  指代={hits or '无'} 载荷={'有' if payload else '无'}  {why}")

    print("\n✅ 自检通过：**指代词检测**（机械）已被证伪过；"
          "\n   **是否自足**（判断）不在断言内 —— 它本来就判不了，只出候选。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", default=str(DEFAULT_SET))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    items, ref_neg, ref_pos = scan(args.eval_set)
    n = report(items, ref_neg, ref_pos)
    # 退出码 1 = 有反例带指代词 ⇒ **有待人判的活**。用于 CI 时即"需人工复核"。
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_public_hygiene —— 公开仓卫生校验（**唯一校验真相，CI 与本地共用**）

为什么需要它
------------
本仓是 **public**，但内容来自**真实协作过程** —— 里面天然会出现
私有仓名、内部目录名、个人绝对路径。**靠人记得脱敏是"提醒"，靠这个脚本才是"结构"。**

检查项
------
  1. 用户绝对路径   `/Users/<name>/...`
  2. 家目录路径     `~/...`
  3. 邮箱
  4. 密钥样式
  5. 手机号
  6. 私有名（**可选**） —— 读 `tools/private-names.txt`，每行一个名字

⚠️ **第 6 项的分工（重要，别误会）**
   `tools/private-names.txt` **被 .gitignore 排除，不进仓库**
   —— 因为它列的就是"不能公开的名字"，写进仓库等于**检查器自己泄露**。

   ⇒ 所以：
      - **本地 pre-commit**：6 项全能跑
      - **CI**：只能跑 1–5 项；**第 6 项在 CI 上是空的**
   ⇒ 这是**有意的取舍**，不是漏洞。详见 README §四。

⛔ **但"空"必须是说出来的，不能是默认发生的**
   清单缺失时**静默跳过**，是本项目点名要防的形状 ——
   **该有输入的地方没有输入，系统不报错、用默认值继续，产出看起来完全正常。**
   ⇒ 所以：**清单缺失 ⟹ 默认直接报错**；
      只有**显式传 `--no-private-list`** 才允许跳过（CI 用），并打印声明。

用法
----
    python3 tools/check_public_hygiene.py                # 校验（本地与 CI 同一份）
    python3 tools/check_public_hygiene.py --self-test    # 突变验证：证明违规时真的会变红
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
PRIVATE_NAMES = ROOT / "tools" / "private-names.txt"

# ── 检查项 1–5：通用模式（**只放通用写法，不得把具体私有名写进本文件**）──
PATTERNS = [
    ("用户绝对路径", re.compile(r"/Users/[A-Za-z0-9_.\-]+/\S*")),
    # ⚠️ 这一条的目的是「**别暴露你的个人目录结构**」，**不是「别写 `~`」**。
    #    所以放行**通用工具目录** —— 任何机器都有、不泄露个人信息，
    #    而本仓的文档**必须**能写它们（否则安装说明没法写）。
    #    目前只放行 `~/.claude/`；其余 `~/...` 一律报。
    ("家目录路径",   re.compile(r"~/(?!\.claude/)[^\s`)\"\]]+")),
    ("邮箱",         re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")),
    ("密钥样式",     re.compile(
        r"sk-[A-Za-z0-9_\-]{8,}"
        r"|ghp_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY")),
    ("手机号",       re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
]

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".json", ".yml", ".yaml",
            ".toml", ".cfg", ".ini", ".html", ".css", ".js"}

# ── 豁免（**名单越短越好，每一条都要能说清理由**）──
ALL_CATS = {"用户绝对路径", "家目录路径", "邮箱", "密钥样式", "手机号", "私有名"}

#   本检查器自身：它按定义就写着那些模式串，**且 --self-test 的样本里必须有"像密钥的东西"**
#   ⇒ 只能整体豁免。
#   ⚠️ 代价：本文件里的**真**泄漏不会被它自己发现 ⇒ **改它时要人工过一眼**。
EXEMPT_SELF = ALL_CATS

#   README：它是**约束文件**，必须举反例（`/Users/…`、`~/…` 这些写法本身）。
#   ⚠️ 同样代价：README 里若真写了某个人的绝对路径，本检查器看不见。
EXEMPT_README = {"用户绝对路径", "家目录路径"}


def load_private_names():
    """第 6 项：私有名清单。**文件不进仓库** ⇒ CI 上通常为空。"""
    if not PRIVATE_NAMES.exists():
        return []
    out = []
    for ln in PRIVATE_NAMES.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        # 清单文件本身：**它按定义就列着那些名字** —— 扫它必然全是假阳性。
        # 且它被 .gitignore 排除、永不公开 ⇒ 整份跳过。
        if p == PRIVATE_NAMES:
            continue
        if p.suffix.lower() not in TEXT_EXT:
            continue
        yield p


def scan_text(text, private_names, exempt=()):
    """返回 [(类别, 行号, 命中文本)]"""
    hits = []
    for i, ln in enumerate(text.split("\n"), 1):
        for name, pat in PATTERNS:
            if name in exempt:
                continue
            for m in pat.finditer(ln):
                hits.append((name, i, m.group(0)))
        for pn in private_names:
            if pn and pn in ln:
                hits.append(("私有名", i, pn))
    return hits


def check():
    private_names = load_private_names()
    all_hits = []
    for p in iter_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if p == SELF:
            exempt = EXEMPT_SELF
        elif p.name.lower().startswith("readme"):
            exempt = EXEMPT_README
        else:
            exempt = ()
        for name, line, tok in scan_text(text, private_names, exempt):
            all_hits.append((p.relative_to(ROOT), line, name, tok))
    return all_hits, private_names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="突变验证：证明检查器在违规时真的会变红")
    ap.add_argument("--no-private-list", action="store_true",
                    help="**显式**声明「本次不做私有名检查（第 6 项）」。"
                         "CI 传这个；**本地不要传** —— 本地应当把清单建出来。")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    hits, private_names = check()

    # ⛔ 清单缺失 ≠ 可以静默跳过。见文件头「但"空"必须是说出来的」。
    if not private_names and not args.no_private_list:
        print("✗ 未通过：找不到 tools/private-names.txt —— **「私有名」这一项会是空的**。")
        print()
        print("  这不是能静默跳过的事：清单缺失时这道防线会**无声失效**，")
        print("  而校验照样打绿 —— 那正是本项目要防的「默认值填充」。")
        print()
        print("  本地：建 tools/private-names.txt，每行一个不能公开的名字（# 开头为注释）。")
        print("  CI  ：已显式传 --no-private-list 声明该缺口")
        print("        （见 .github/workflows/hygiene-check.yml）。")
        return 1

    print(f"公开卫生校验 · 根目录 {ROOT.name}/")
    if private_names:
        print(f"  私有名清单：已加载 {len(private_names)} 条（本地门）")
    else:
        print("  ⚠️ 私有名清单：**已显式跳过**（--no-private-list）—— 第 6 项本次为空，")
        print("     这是**声明的缺口**，不是通过。")
    print()

    if not hits:
        print("✅ 通过：绝对路径 / 家目录路径 / 邮箱 / 密钥样式 / 手机号"
              + ("/ 私有名" if private_names else "（私有名已显式跳过）") + " —— 全部无命中")
        return 0

    print(f"✗ 未通过（{len(hits)} 处）：\n")
    for rel, line, name, tok in hits:
        print(f"  [{name}] {rel}:{line}")
        print(f"      {tok[:100]}")
    print("\n约束见 README §二（占位符规范）。")
    return 1


# ══════════════════════════════════════════════════════════════════
# 突变验证：**检查器自己也要有防腐**
# —— 证明"违规时真的会变红"，而不是永远打印 ✅
# ══════════════════════════════════════════════════════════════════
CASES = [
    # (说明, 文本, 期望检出的类别)
    ("违规 · 用户绝对路径", "见 /Users/someone/project/x.md", "用户绝对路径"),
    ("违规 · 家目录路径", "路径是 ~/Documents/secret.md", "家目录路径"),
    ("违规 · 邮箱", "联系 someone@example.com", "邮箱"),
    ("违规 · 密钥样式", "token: sk-abcdefghijklmnop", "密钥样式"),
    ("违规 · 手机号", "电话 13800138000", "手机号"),
    ("违规 · 私有名", "项目 SomePrivateRepo 在这里", "私有名"),
    # 合规样本（不得误报）
    ("合规 · 占位符", "见 <项目根>/docs/x.md", None),
    ("合规 · 相对路径", "见 tools/check.py", None),
    ("合规 · 日期", "2026-09-15 建立", None),
    ("合规 · 普通词", "dry-run 模式 / pre-commit 钩子", None),
]


def self_test():
    print("check_public_hygiene · 突变验证\n")
    ok = fail = 0
    for desc, text, expect in CASES:
        names = ["SomePrivateRepo"]
        got = {n for n, _, _ in scan_text(text, names)}
        good = (expect in got) if expect else (not got)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        mark = "✅" if good else "❌"
        print(f"  {mark} {desc:<22} 期望={expect or '无'}  实际={sorted(got) or '无'}")

    # 反证：把私有名清单清空 → 「私有名」这一类必须**检不出来**
    # （证明它真的依赖清单，而不是永远命中）
    got = {n for n, _, _ in scan_text("项目 SomePrivateRepo 在这里", [])}
    good = "私有名" not in got
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"  {'✅' if good else '❌'} {'反证 · 清空清单后不报':<22} 期望=无  实际={sorted(got) or '无'}")

    print(f"\n  通过 {ok} · 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

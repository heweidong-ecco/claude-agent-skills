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
import subprocess
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


def git_ignored(paths):
    """问 git：这些路径里哪些**不会进仓库**。返回 `set`；**问不到就返回 `None`**。

    为什么需要它
    ------------
    本检查器管的是「**公开**卫生」—— 只该管**会被发布的东西**。
    但 `rglob` 扫的是**整个工作区**，于是把 `.gitignore` 掉的文件也算进来。

    **实测（2026-09-15）**：92 处报警**全在 gitignore 的目录里**（测试转录）。
    本地 pre-commit 因此**拒了提交**，而 CI（干净 clone 里那些文件不存在）**照样过**。
    ⇒ **同一份脚本，本地比 CI 严。**

    后果**不是"更安全"**，是两条路都通向门失效：

    - 要么 `--no-verify` 变成习惯
    - 要么有人把门改松 —— **那 CI 也跟着松**

    这正是文件头警告的「门会自己腐烂」。

    ⚠️ 返回 `None` = **问不到 git**（未装 / 不是仓库）。此时**不跳过**，
       并**把范围退回"整个工作区"这件事明说出来** —— **宁可吵，不可漏**。
    """
    if not paths:
        return set()
    try:
        # ⚠️ `-z` **不是可选的**：默认输出会给非 ASCII 路径加引号并转成八进制转义
        #    （`"…/\350\257\257\346\212\245…"`），于是**中文目录名一个都匹配不上**。
        #    实测（2026-09-15）：去掉 `-z` 时，92 处报警仍全在 gitignore 的目录里 ——
        #    **门看着改好了，其实没生效**。`-z` 让输入输出都按 NUL 分隔、**原样不转义**。
        p = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=ROOT,
            input="\0".join(str(x) for x in paths) + "\0",
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 0 = 有被忽略的 / 1 = 一个都没有 / 其余（含 128）= 问不到
    if p.returncode not in (0, 1):
        return None
    return {x for x in p.stdout.split("\0") if x.strip()}


def iter_files():
    """返回 `(待扫文件列表, 因 gitignore 跳过的个数)`；第二个为 `None` = **范围退回整个工作区**。

    ⛔ 跳过**必须可见** —— 仓里硬约束 #2（不许静默跳过检查）与
       §5 那条「没有数据的格子必须显示成没数据」，这里是同一个道理。
    """
    cands = []
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
        cands.append(p)

    rel = {p: str(p.relative_to(ROOT)) for p in cands}
    ignored = git_ignored(list(rel.values()))
    if ignored is None:
        return cands, None
    keep = [p for p in cands if rel[p] not in ignored]
    return keep, len(cands) - len(keep)


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
    files, n_skipped = iter_files()
    all_hits = []
    for p in files:
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
    return all_hits, private_names, n_skipped


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

    hits, private_names, n_skipped = check()

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
    # ⛔ 扫描范围**必须说出来**。跳过不报 = 静默跳过（硬约束 #2 禁的正是这个）。
    if n_skipped is None:
        print("  ⚠️ 扫描范围：**整个工作区** —— 问不到 git（未装 / 不是仓库），"
              "**没能排除 `.gitignore` 的文件**。")
        print("     这会让门**比 CI 严**（CI 在干净 clone 上跑，没有那些文件）"
              "⇒ 本地可能被拒而 CI 通过。")
    else:
        print(f"  扫描范围：仅**会被提交的**文件 —— 依 `.gitignore` 跳过 {n_skipped} 个")
    if private_names:
        print(f"  私有名清单：已加载 {len(private_names)} 条（本地门）")
    else:
        print("  ⚠️ 私有名清单：**已显式跳过**（--no-private-list）—— 第 6 项本次为空，")
        print("     这是**声明的缺口**，不是通过。")
    print()

    if not hits:
        print("✅ 通过：绝对路径 / 家目录路径 / 邮箱 / 密钥样式 / 手机号"
              + (" / 私有名" if private_names else "（私有名已显式跳过）") + " —— 全部无命中")
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

    # ── 范围判据（2026-09-15 新增）────────────────────────────────────
    # 证明「只扫**会被提交的**东西」这条路真的通，而不是永远返回空集。
    print("\n  范围判据 · 依 .gitignore 排除（本次新增）：")
    # ⚠️ 探针**必须含非 ASCII**。第一版探针是纯 ASCII（`probe-workspace/probe.md`），
    #    **它全绿**，而真实检查里中文目录**一个都没被排除** ——
    #    git 默认给非 ASCII 路径加引号 + 八进制转义，比对必然落空。
    #    ⇒ **一条测不到真实输入形状的探针 = 又一次"看着像测过"的自检。**
    probes = [
        ("忽略的 · ASCII 路径", "grilling-agent/source-layer/probe-workspace/probe.md", True),
        ("忽略的 · **非 ASCII 路径**", "某目录/probe-workspace/中文名.md", True),
        ("反证 · 未忽略的不被误排", "README.md", False),
    ]
    got = git_ignored([q for _, q, _ in probes])
    if got is None:
        print("  ⚠️ 问不到 git ⇒ **本次无法验证范围判据**。"
              "把这件事说出来，**不当作通过**。")
    else:
        for desc, q, want_in in probes:
            good = (q in got) == want_in
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
            print(f"  {'✅' if good else '❌'} {desc:<22} {q}")

    print(f"\n  通过 {ok} · 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run-trigger-test —— 触发率测试（**替代 skill-creator 的 run_loop**）

为什么不用 run_loop
-------------------
它的机制是**往 `<项目根>/.claude/commands/` 写一个临时 command 文件**，
再检测模型有没有调 `Skill` 工具。

**但 commands ≠ skills** —— 实测（2026-09-15）：
同一份 skill、同一条 query，**装成 `.claude/skills/` 时 3/3 触发**，
而 run_loop 报 **0/3**。⇒ **它的测量无效，五轮全白跑。**

本脚本**直接跑真 skill**，并且**记录"被谁抢走了"** ——
因为「没触发」和「触发到别的 skill 上」是**两种不同的病**，只有分开记才看得出来。

⚠️ 第一版本脚本**自己也是坏的**（同一天修的，记在这里免得重犯）
------------------------------------------------------------------
症状：跑出 recall=40%，其中 36/60 次是 `<超时>`；6 条"该触发"全报 0/3。

**根因两条，都是量具的错，不是 skill 的错：**

1. 用 `subprocess.run(...)` ⇒ **非等进程结束不可**。
   而 `run_once` 的注释里写着"不必等执行完" —— **注释和实现是矛盾的**。
   实际发生的是：模型调完 skill **就去真干活了**，一干五分钟往上 ⇒ 超时。
   **⇒ 超时的那几条，恰恰是触发得最干脆的那几条。**
   复测其一：报"0/3 超时"的那条，**6.4 秒就调了 `grilling-agent`**。
2. 超时分支 `return ["<超时>"]` ⇒ **把已经读到的 stdout 整个丢掉**。
   前 300 秒里调没调 skill，**有数据也扔了**，只剩一个字"超时"。

**⇒ 教训（这条比脚本本身值钱）：**
> **量具坏掉的方向，正好是让被测对象显得更差的方向时，最容易骗过自己 ——**
> 因为"它不行"看起来像结论，不像故障。
> 所以**凡是没有数据的格子，必须显示成"没数据"，不能显示成"没做到"。**

修法见 `run_once()`：流式读、见到 Skill 调用就提前收工、超时保留已见数据。

用法
----
    # 前提：目标 skill 已装到 <cwd>/.claude/skills/<name>/
    python3 run-trigger-test.py --cwd <装着它的目录>

    python3 run-trigger-test.py --cwd <目录> --runs 5 --only-trigger
    python3 run-trigger-test.py --cwd <目录> --cap 600      # 放宽单次上限
"""

import argparse
import collections
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SET = HERE / "触发查询.json"

# 见到第一个 Skill 调用后再多读这么久。
# **为什么不是立刻杀**：模型可能连调两个 skill（先 brainstorming 再 grilling-agent）。
# 立刻杀会把后者漏掉，于是把"其实触发了"误报成"被抢走"。
GRACE = 20.0


def load_set(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else raw.get("evals", raw.get("queries", []))


def _skill_calls(line, out):
    """从一行 stream-json 里抠出 Skill 调用名，追加进 out。"""
    try:
        d = json.loads(line)
    except Exception:
        return
    m = d.get("message") if isinstance(d, dict) else None
    c = m.get("content") if isinstance(m, dict) else None
    if not isinstance(c, list):
        return
    for b in c:
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
            out.append((b.get("input") or {}).get("skill", "?"))


def run_once(query, cwd, model=None, cap=300):
    """跑一次，返回 `(被调用的 skill 名列表, 结束方式)`。

    结束方式：
      "done"     —— 正常跑完
      "stopped"  —— 见到 skill 调用 + 宽限窗口后主动收工（**数据有效**）
      "timeout"  —— 到点被杀，**且一个 skill 都没见到**（**这一格没有数据**）
      "no_cli"   —— 没装 claude

    ⛔ 关键：超时时**保留已读到的内容**，不返回哨兵值把数据抹掉。
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    cmd = ["claude", "-p", query, "--output-format", "stream-json", "--verbose"]
    if model:
        cmd += ["--model", model]

    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, cwd=cwd, bufsize=1,
        )
    except FileNotFoundError:
        return [], "no_cli"

    called = []
    killed = threading.Event()
    timer = {"t": None}

    def kill():
        killed.set()
        try:
            p.kill()
        except Exception:
            pass

    def arm(sec):
        if timer["t"]:
            timer["t"].cancel()
        timer["t"] = threading.Timer(sec, kill)
        timer["t"].daemon = True
        timer["t"].start()

    arm(cap)
    t0 = time.time()
    try:
        for ln in p.stdout:                       # ← 流式：不必等它做完
            _skill_calls(ln, called)
            if called and not killed.is_set():
                arm(GRACE)                        # 见到调用 ⇒ 换成宽限窗口
        # 循环结束 = 进程自己退了
    finally:
        if timer["t"]:
            timer["t"].cancel()
        try:
            p.kill()
            p.wait(timeout=5)
        except Exception:
            pass

    if killed.is_set():
        return called, ("stopped" if called else "timeout")
    _ = time.time() - t0
    return called, "done"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", default=str(DEFAULT_SET))
    ap.add_argument("--cwd", required=True, help="装着这个 skill 的目录（其 .claude/skills/ 下）")
    ap.add_argument("--skill", default="grilling-agent", help="被测的 skill 名")
    # 默认 5 而不是 3：**3 次分不出「噪音」和「真实倾向」**。
    # 实测（2026-09-15）：同一条查询两轮跑出 2/3 与 0/3，3 次根本判不了。
    # 样本量不够时得到的"率"，看起来像结论，其实是抽样抖动。
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--cap", type=float, default=300, help="单次硬上限（秒）")
    ap.add_argument("--model", default=None)
    ap.add_argument("--only-trigger", action="store_true", help="只跑 should_trigger=true 的")
    args = ap.parse_args()

    items = load_set(args.eval_set)
    if args.only_trigger:
        items = [x for x in items if x.get("should_trigger")]

    print(f"触发率测试 · skill={args.skill} · 每条 {args.runs} 次 · 单次上限 {args.cap:.0f}s")
    print(f"  cwd = {args.cwd}")
    print("─" * 78)

    hit_yes = tot_yes = 0
    hit_no = tot_no = 0
    nodata = 0
    stolen = collections.Counter()

    for x in items:
        q = x.get("query", "")
        want = bool(x.get("should_trigger"))
        mine = 0
        others = collections.Counter()
        hows = collections.Counter()
        for _ in range(args.runs):
            called, how = run_once(q, args.cwd, args.model, args.cap)
            hows[how] += 1
            if how == "timeout":
                nodata += 1
            if args.skill in called:
                mine += 1
            for c in set(called):
                if c != args.skill:
                    others[c] += 1
                    stolen[c] += 1
        ok = (mine > 0) == want
        mark = "✅" if ok else "❌"
        extra = f"  抢走: {dict(others)}" if others else ""
        unsure = f"  ⚠️无数据 {hows['timeout']}次" if hows["timeout"] else ""
        print(f"  {mark} [{'该触发' if want else '不该  '}] {mine}/{args.runs}  {q[:40]}{extra}{unsure}")
        if want:
            tot_yes += 1
            hit_yes += 1 if mine > 0 else 0
        else:
            tot_no += 1
            hit_no += 1 if mine > 0 else 0

    print("─" * 78)
    recall = hit_yes / tot_yes if tot_yes else 0
    fp = hit_no / tot_no if tot_no else 0
    print(f"  该触发的命中：{hit_yes}/{tot_yes}  = {recall:.0%}   ← recall（低了=该用不用）")
    print(f"  不该触发的误触：{hit_no}/{tot_no} = {fp:.0%}   ← 误报（高了=乱摸）")
    if nodata:
        print(f"  ⛔ 无数据的格子：{nodata} 格（超时且未见到任何 skill）"
              f" —— 这些格子**不算失败**，要单独看，别混进上面的率里")
    if stolen:
        print(f"  ⚠️ 被别的 skill 抢走：{dict(stolen)}")
        print("     （这一项 run_loop 看不见 —— 它只判'有没有调这个'，不判'调了谁'）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

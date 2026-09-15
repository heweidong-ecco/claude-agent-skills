#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capture-run —— 把一次真实运行**从头到尾**存下来

为什么需要它
------------
`run-trigger-test.py` 只回答一个问题：**调没调这个 skill**。
它见到调用就提前收工，**不保留任何过程** —— 这是刻意的，为了跑得快。

但它因此回答不了下一个问题：**"调了之后，模型到底干了什么？"**
光知道"误报了 4 条"，改不动描述 —— 得看见模型在那 4 条里**具体说了什么**，
才知道该往 `description` 里补哪一句。

⚠️ 和 `run-trigger-test.py` 的分工，别混：
    run-trigger-test.py  · 快 · 只要结论 · **见到就杀**
    capture-run.py       · 慢 · 要全过程 · **跑到底，不杀**

用法
----
    python3 capture-run.py --cwd <目录> --out <输出目录> --attempts 3 \
        --query "你看这个截图里报的是端口被占用了对吧"

每个 query 最多跑 `--attempts` 次，**每次都留档**（不管触没触发）——
因为"没触发的那次"和"触发的那次"**差在哪里**，本身就是信息。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path


def slug(s, n=28):
    s = re.sub(r"[^\w一-鿿]+", "-", s).strip("-")
    return s[:n] or "query"


def run_full(query, cwd, cap):
    """跑到底（不提前杀），返回 (原始行列表, 被调用的 skill 列表, 结束方式)。"""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    cmd = ["claude", "-p", query, "--output-format", "stream-json", "--verbose"]
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=cwd, bufsize=1,
    )

    killed = threading.Event()

    def kill():
        killed.set()
        try:
            p.kill()
        except Exception:
            pass

    t = threading.Timer(cap, kill)
    t.daemon = True
    t.start()

    lines, skills = [], []
    try:
        for ln in p.stdout:
            lines.append(ln)
            try:
                d = json.loads(ln)
            except Exception:
                continue
            m = d.get("message") if isinstance(d, dict) else None
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                        skills.append((b.get("input") or {}).get("skill", "?"))
    finally:
        t.cancel()
        try:
            p.kill()
            p.wait(timeout=5)
        except Exception:
            pass

    return lines, skills, ("timeout" if killed.is_set() else "done")


def to_markdown(lines):
    """把 stream-json 摊成能读的对话：谁说了什么、调了什么工具。"""
    out = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        typ = d.get("type")
        if typ == "system":
            sub = d.get("subtype", "system")
            # ⚠️ thinking_tokens 每思考一小段就发一条，一次运行能有 1800+ 条。
            # 写进正文会把真内容挤到几十屏之后 —— **看着像空的，其实是有内容**。
            # 实测（2026-09-15）：因此误判了一次"抽取失败"。
            if sub != "thinking_tokens":
                out.append(f"> _[{sub}]_\n")
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        role = m.get("role", "?")
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                out.append(f"### {role}\n\n{b.get('text','').strip()}\n")
            elif bt == "tool_use":
                inp = b.get("input") or {}
                s = json.dumps(inp, ensure_ascii=False)
                out.append(f"**[工具] `{b.get('name')}`**\n\n```json\n{s[:1200]}\n```\n")
            elif bt == "tool_result":
                r = b.get("content")
                if isinstance(r, list):
                    r = " ".join(x.get("text", "") for x in r if isinstance(x, dict))
                r = str(r or "")
                out.append(f"```\n[结果] {r[:1500]}\n```\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--query", action="append", required=True)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--cap", type=float, default=300)
    args = ap.parse_args()

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)

    summary = []
    for i, q in enumerate(args.query, 1):
        d = root / f"{i:02d}-{slug(q)}"
        d.mkdir(exist_ok=True)
        hits = 0
        for k in range(1, args.attempts + 1):
            t0 = time.time()
            lines, skills, how = run_full(q, args.cwd, args.cap)
            el = time.time() - t0
            (d / f"run{k}.transcript.jsonl").write_text("".join(lines), encoding="utf-8")
            (d / f"run{k}.md").write_text(
                f"# {q}\n\n> 第 {k} 次 · {el:.0f}s · 结束方式={how} · "
                f"调用的 skill={skills or '（无）'}\n\n" + to_markdown(lines),
                encoding="utf-8",
            )
            if "grilling-agent" in skills:
                hits += 1
            print(f"  [{i}/{len(args.query)}] run{k}: {el:.0f}s  skill={skills or '（无）'}", flush=True)
        (d / "query.txt").write_text(q, encoding="utf-8")
        summary.append((q, hits, args.attempts))
        print(f"⇒ {q[:40]}  触发 {hits}/{args.attempts}", flush=True)

    sm = ["# 抓取汇总", ""]
    for q, h, n in summary:
        sm.append(f"- 触发 **{h}/{n}** — {q}")
    (root / "汇总.md").write_text("\n".join(sm), encoding="utf-8")
    print(f"\n存到 {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

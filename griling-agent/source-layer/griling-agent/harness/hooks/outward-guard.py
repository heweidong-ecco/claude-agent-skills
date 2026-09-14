#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outward-guard —— ① 对外 / 不可逆动作门（`PreToolUse` · 挂 `Bash`）

拦什么：**会写进历史、或发出去、或删掉拿不回来的动作**。
不拦什么：读操作、本地查询、白名单内的普通命令。

判据（**不是"看起来危险"，是"做错了收不回来"**）：
  · 写进版本历史   `git commit` / `git tag`
  · 发到外面       `git push` / `gh repo create` / `gh pr merge` / `gh release create`
  · 删掉拿不回     `rm -rf`（**系统临时目录除外** —— 那里的东西本来就是易失的）

⚠️ **`git commit` 本可回滚**，这里仍然拦 —— 因为"提交了不该提交的东西"
   会把一个**临时的错误**变成**一条历史**。而历史比工作区难清。

⚠️ 定位：**防手滑，不防对抗**。绕过方式：`$(...)`、变量拼接、别名、写进脚本再执行。

⚠️ 子 Agent：**只注入 `additionalContext`，不 ask**（子 Agent 无人可批准，ask 会挂死）。

自测：python3 outward-guard.py --self-test
"""

import json
import os
import re
import sys

SYSTEM_ROOTS = ["/tmp", "/var", "/dev", "/private/tmp", "/private/var"]

# (类别, 正则, 说明) —— **顺序即优先级**
PATTERNS = [
    ("git 强推",  re.compile(r"\bgit\s+push\b[^\n|;&]*\s(?:--force|-f)\b"),
     "强推会覆盖远端历史，且别人已经拉走的提交收不回来。"),
    ("git 推送",  re.compile(r"\bgit\s+push\b"),
     "推送 = **发到外面**。一旦推上去，可能已被缓存/索引，删了也不等于没发生。"),
    ("gh 建仓",   re.compile(r"\bgh\s+repo\s+create\b"),
     "建的是**公共可见**的东西。"),
    ("gh 合并",   re.compile(r"\bgh\s+pr\s+merge\b"),
     "合并会**落地到主干**。"),
    ("gh 发布",   re.compile(r"\bgh\s+release\s+create\b"),
     "发布 = 对外。"),
    ("git 打标",  re.compile(r"\bgit\s+tag\b"),
     "标签通常指向「要给别人用的版本」。"),
    ("git 提交",  re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*commit\b"),
     "提交会把**临时的错误**变成**一条历史** —— 历史比工作区难清。"),
]

RE_RM = re.compile(r"\brm\s+(?:-[a-zA-Z]+\s+)*([^\s;&|]+)")
RE_RM_RF = re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*")


def rm_targets(cmd):
    """取出 `rm -r/-f` 的目标；系统临时目录下的不算命中。"""
    if not RE_RM_RF.search(cmd):
        return []
    out = []
    for m in RE_RM.finditer(cmd):
        tok = m.group(1).strip().strip("'\"")
        if not tok or tok.startswith("-"):
            continue
        p = os.path.realpath(os.path.expanduser(tok) if tok.startswith("~")
                             else os.path.abspath(tok))
        if not any(p == r or p.startswith(r + os.sep) for r in SYSTEM_ROOTS):
            out.append(tok)
    return out


def find_hits(payload):
    if payload.get("tool_name") != "Bash":
        return []
    cmd = (payload.get("tool_input") or {}).get("command", "") or ""
    hits = []
    for name, pat, why in PATTERNS:
        if pat.search(cmd):
            hits.append((name, why))
            break                       # 只报最高优先的那一条，避免刷屏
    for t in rm_targets(cmd):
        hits.append(("不可逆删除", f"`rm -r/-f {t}` —— 系统临时目录之外，删了拿不回来。"))
    return hits


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
        emit(f"outward-guard 无法解析钩子输入（{e}），请人工确认", None)
        return 0
    hits = find_hits(payload)
    if not hits:
        return 0
    body = "\n".join(f"  · [{n}] {w}" for n, w in hits)
    emit("⛔ outward-guard：这是**对外 / 不可逆**的动作，需要人工确认。\n\n"
         f"{body}\n\n"
         "若已获授权，批准即可。**若只是探索性操作，请换成不落地的做法。**",
         (payload.get("agent_type") or "").strip())
    return 0


# ══════════════════════════════════════════════════════════════════
def self_test():
    cases = [
        # 应拦
        ("git push",                 "git push origin main",                     1),
        ("git push --force",         "git push --force origin main",             1),
        ("git commit",               'git commit -m "x"',                        1),
        ("git tag",                  "git tag v1.0.0",                           1),
        ("gh repo create",           "gh repo create foo --public",              1),
        ("gh pr merge",              "gh pr merge --auto --squash",              1),
        # ⚠️ 样本用占位符，不用家目录路径 —— 既符合占位符规范，
        #    也不会让公开卫生门**误伤自己**（它按定义就要拦家目录路径）。
        ("rm -rf 非临时区",           "rm -rf <项目根>/mywork",                   1),
        ("rm -rf 相对路径",           "rm -rf ./build",                           1),
        # 应放
        ("git status",               "git status",                               0),
        ("git log",                  "git log --oneline",                        0),
        ("git diff",                 "git diff HEAD",                            0),
        ("gh pr view",               "gh pr view 1",                             0),
        ("rm -rf /tmp",              "rm -rf /tmp/scratch",                      0),
        ("rm 单文件（无 -r/-f）",      "rm notes.txt",                            0),
        ("普通命令",                  'grep -rn "x" .',                           0),
    ]
    print("outward-guard · 突变验证\n")
    ok = fail = 0
    for desc, cmd, expect in cases:
        got = 1 if find_hits({"tool_name": "Bash", "tool_input": {"command": cmd}}) else 0
        good = got == expect
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print(f"  {'✅' if good else '❌'} {desc:<22} 期望={'拦' if expect else '放'}  实际={'拦' if got else '放'}")

    # 反证：非 Bash 工具必须**永远不命中**（证明它真的只挂 Bash）
    got = 1 if find_hits({"tool_name": "Read", "tool_input": {"command": "git push"}}) else 0
    good = got == 0
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"  {'✅' if good else '❌'} {'反证 · 非 Bash 工具不命中':<22} 期望=放  实际={'拦' if got else '放'}")

    print(f"\n  通过 {ok} · 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

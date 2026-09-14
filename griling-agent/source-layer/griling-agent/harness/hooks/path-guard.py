#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
path-guard —— ③ 路径白名单门（`PreToolUse`）

拦什么：**白名单之外的本地路径**（读 / 写 / 编辑 / 列目录）。
不拦什么：**GitHub / 网络**（无本地路径）；白名单以内的路径。

⚠️ 定位：**防手滑，不防对抗**。已知绕过：`$(...)`、变量拼接、编码解码、
   重定向的间接写入、以及**任何不经过工具的访问**。

⚠️ 子 Agent：**只注入 `additionalContext`，不 ask** —— 子 Agent 无人可批准，ask 会把它挂死。

⛔ **装之前必改**：下面 `ALLOW_ROOTS`。不改 = 白名单是别人的路径。
⛔ 为什么挂 7 个工具而不是只挂 Bash：**不 `cd` 也能读**；且 Read/Write/Edit
   根本不走 Bash —— 只挂 Bash 等于没挂。

自测：python3 path-guard.py --self-test
"""

import json
import os
import re
import sys

# ══════════════════════════════════════════════════════════════════
# ⛔ 装之前必改：本项目的白名单根（**真路径化，防 `..` 与符号链接绕过**）
# ══════════════════════════════════════════════════════════════════
HOME = os.path.expanduser("~")
ALLOW_ROOTS = [
    os.path.realpath(os.path.join(HOME, "Desktop", "<在此填你的项目根>")),
]

# ── 系统目录：放行，否则脚本/工具自己都跑不了，会全是噪音 ──
# ⚠️ macOS 上 `/tmp` `/etc` `/var` 是 `/private/*` 的软链；
#    本脚本会先 realpath ⇒ **必须把 `/private/*` 也列上**（自测抓出来的）。
SYSTEM_ROOTS = [
    "/usr", "/bin", "/sbin", "/opt/homebrew", "/opt/local",
    "/System", "/Library", "/Applications", "/cores",
    "/tmp", "/var", "/etc", "/dev",
    "/private/tmp", "/private/var", "/private/etc",
]

PATH_FIELDS = {
    "Read": ("file_path",), "Write": ("file_path",), "Edit": ("file_path",),
    "NotebookEdit": ("notebook_path",), "Glob": ("path",), "Grep": ("path",),
}

# Bash 里的路径 token：`/` 必须紧跟 空白/引号/=/左括号
# —— 这样 `https://github.com/x` 的 `//` 不会被误判（它前面是 `:`）
RE_ABS = re.compile(r"""(?<=[\s"'=(])(/[^\s"';&|)`]+)""")
RE_TILDE = re.compile(r"""(?<=[\s"'=(])(~[^\s"';&|)`]*)""")
RE_CD = re.compile(r"""\b(?:cd|pushd)\s+(?:-{1,2}\S+\s+)*([^\s;&|]+)""")


def under(path, roots):
    for r in roots:
        if path == r or path.startswith(r + os.sep):
            return True
    return False


def resolve(tok, cwd):
    tok = tok.strip().strip("'\"")
    if not tok:
        return None
    tok = tok.replace("$HOME", HOME).replace("${HOME}", HOME)
    if tok.startswith("~"):
        tok = os.path.join(HOME, tok[1:].lstrip("/"))
    if not os.path.isabs(tok):
        tok = os.path.join(cwd, tok)
    try:
        return os.path.realpath(tok)
    except Exception:
        return None


def hits_from_bash(cmd, cwd):
    cleaned = re.sub(r"\bhttps?://\S+", " ", cmd)      # 抹掉 URL
    toks = RE_CD.findall(cleaned) + RE_ABS.findall(cleaned) + RE_TILDE.findall(cleaned)
    out = []
    for t in toks:
        p = resolve(t, cwd)
        if p and not under(p, ALLOW_ROOTS) and not under(p, SYSTEM_ROOTS):
            out.append((t, p))
    return out


def emit(reason, agent_type):
    """**故意不对称**：主会话 ask；子 Agent 只注入提示（否则挂死）。"""
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


def find_hits(payload):
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd") or os.getcwd()
    hits = []
    for field in PATH_FIELDS.get(tool, ()):
        v = ti.get(field)
        if isinstance(v, str) and v:
            p = resolve(v, cwd)
            if p and not under(p, ALLOW_ROOTS) and not under(p, SYSTEM_ROOTS):
                hits.append((v, p))
    if tool == "Bash":
        hits += hits_from_bash(ti.get("command", "") or "", cwd)
    return hits


def main():
    if "--self-test" in sys.argv:
        return self_test()

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        # 解不开 → 保守起见交人看（fail-closed）
        emit(f"path-guard 无法解析钩子输入（{e}），请人工确认", None)
        return 0

    hits = find_hits(payload)
    if not hits:
        return 0                      # 静默放行，不表态
    lines = "\n".join(f"  · {t}  →  {p}" for t, p in hits)
    emit("⛔ path-guard：该操作指向**白名单之外**的路径，需要人工确认。\n"
         f"\n命中：\n{lines}\n\n"
         "白名单内的路径不会触发；网络来源（GitHub 等）无本地路径、不受本门约束。",
         (payload.get("agent_type") or "").strip())
    return 0


# ══════════════════════════════════════════════════════════════════
# 突变验证：证明违规时**真的会变红**，而不是永远静默
# ══════════════════════════════════════════════════════════════════
def self_test():
    global ALLOW_ROOTS          # ⚠️ 必须放在函数体最前 —— 下面的反证要改它
    cases = [
        ("白名单内 · cd",      {"command": f"cd {ALLOW_ROOTS[0]}/sub"},          0),
        ("系统目录 · /tmp",    {"command": "cd /tmp"},                           0),
        ("URL 不算路径",       {"command": "curl -s https://github.com/a/b"},    0),
        ("普通命令无路径",      {"command": 'grep -rn "x" .'},                    0),
        ("⛔ 白名单外 · 绝对路径", {"command": "cat /opt/secret/x.md"},             1),
        # ⚠️ 样本要挑**真正在白名单外**的路径 —— 别用 /etc、/tmp 这些系统目录，
        #    它们本来就被放行，拿它们当违规样本会测出"假失败"。
        ("⛔ 白名单外 · 无 cd 直读", {"command": "cat /opt/secret/y.md"},           1),
        ("⛔ `..` 逃逸",        {"command": "cd ../.."},                          1),
        ("⛔ Read · 白名单外",   None,                                            1),
    ]
    print("path-guard · 突变验证\n")
    ok = fail = 0
    for desc, ti, expect in cases:
        if ti is None:
            payload = {"tool_name": "Read", "cwd": str(ALLOW_ROOTS[0]),
                       "tool_input": {"file_path": "/opt/secret/z.md"}}
        else:
            payload = {"tool_name": "Bash", "cwd": str(ALLOW_ROOTS[0]),
                       "tool_input": ti}
        hits = find_hits(payload)
        got = 1 if hits else 0
        good = got == expect
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print(f"  {'✅' if good else '❌'} {desc:<26} 期望={'拦' if expect else '放'}  实际={'拦' if got else '放'}")

    # 反证：把白名单扩成根 —— 上面那些"违规"必须**全部不再命中**
    saved = ALLOW_ROOTS
    ALLOW_ROOTS = ["/"]
    got = 1 if find_hits({"tool_name": "Bash", "cwd": "/", "tool_input": {"command": "cat /etc/passwd"}}) else 0
    good = got == 0
    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
    print(f"  {'✅' if good else '❌'} {'反证 · 白名单=根后不再命中':<26} 期望=放  实际={'拦' if got else '放'}")
    ALLOW_ROOTS = saved

    print(f"\n  通过 {ok} · 失败 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

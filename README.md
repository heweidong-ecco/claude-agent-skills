# claude-agent-skills

> **一句话结论**：本仓是 **skill 的开发与维护仓库**，不是"拿来即用"的分发库。
> 一个 skill 的完整形态 = `SKILL.md`（意图层）+ `harness/`（可拦的那几层）。
>
> **读者是 Agent。人是维护者，不是读者。**
> 由此：**写规则，不写感想**；**能被扫读**；**不要求读者去本仓之外翻东西**。
>
> ⚙️ **要在本仓干活**（不是只看）→ 先读 **`CLAUDE.md`**：接续协议、更新纪律、当前状态都在那里。

---

## 1 · 目录约定

```
<skill-name>/
  source-layer/                  ← 来源与维护层（**过程**）
    record记录-*.md              ← 原始材料：需求、纠偏、测试、决策
    <skill-name>/                ← 成品层
      SKILL.md                   ← 大写。**只有这一层是拿去加载的**
      harness/                   ← 拦截模版（可拦的那几层）
        README.md                ← ⛔ **安装说明 —— 不装 = 门没接电**
        hooks/*.py
        settings.project.json
        settings.user.json
```

**取用**：

```bash
cp -r <skill-name>/source-layer/<skill-name>/ ~/.claude/skills/
```

---

## 2 · ⛔ 占位符规范（硬性）

**本仓是 public ⇒ 私有项一律用 `<…>` 占位符。**

| 禁止出现 | 换成 |
|---|---|
| 私有仓库名 | `<私有仓>` |
| 内部目录名 | `<内部工作目录>` |
| 用户绝对路径（`/Users/…`） | 相对路径，或 `<项目根>/…` |
| 家目录路径（`~/…`） | 同上 |
| 邮箱 / 密钥 / 手机号 | **一律不写** |

**逐字引用中若必须替换**：在该文件**头部加「脱敏声明」**，写明「除占位符外一字未改」。
⛔ **不许静默改动引文。**

---

## 3 · 四层门禁

| 层 | 文件 | 作用 |
|---|---|---|
| **唯一真相** | `tools/check_public_hygiene.py` | **CI 与本地跑的是同一份** |
| **本地左移门** | `.githooks/pre-commit` | 提交前拦 |
| **终门** | `.github/workflows/hygiene-check.yml` | push / PR 必跑 |
| **防腐** | `check_public_hygiene.py --self-test` | **突变验证**：证明违规时**真的会变红** |

装本地门：`git config core.hooksPath .githooks`

**绕过**：`git commit --no-verify` —— 请说明为何绕过。

---

## 4 · 🔁 换机器（`git clone` 之后必须做的两件事）

| 项 | 会 clone 吗 | 不做的后果 |
|---|---|---|
| `core.hooksPath .githooks` | ❌ **不会**（它是 `.git/config` 里的本地配置） | **门不生效** —— 但 `.githooks/pre-commit` **文件在**，看起来像装好了 |
| `tools/private-names.txt` | ❌ **不会**（被 `.gitignore` 排除） | 本地门**直接报错**（不再静默跳过，见 §5.1） |

```bash
git config core.hooksPath .githooks          # ① 装本地门
$EDITOR tools/private-names.txt              # ② 重建私有名清单（每行一个名字）
python3 tools/check_public_hygiene.py        # ③ 验证两道都活了
```

---

## 5 · ⚠️ 已知局限（**不假装全绿**）

`tools/private-names.txt` **不进仓库** ⇒ **CI 侧「私有名」这一项拦不了**。

| 检查项 | 本地 pre-commit | CI |
|---|---|---|
| 绝对路径 / 家目录路径 / 邮箱 / 密钥 / 手机号 | ✅ | ✅ |
| **私有仓名 / 内部目录名** | ✅ | ❌ |

**有意的取舍** —— 把私有名写进检查器，等于**检查器自己泄露**。

### 5.1 ⛔「空」必须是**说出来的**，不能是**默认发生的**

| 场景 | 行为 |
|---|---|
| 清单存在 | 跑 6 项 |
| **清单缺失 + 未传开关** | **直接报错退出**（本地默认） |
| 清单缺失 + 显式 `--no-private-list` | 跳过，并打印「**这是声明的缺口，不是通过**」 |

**CI 传该开关；本地不要传。**

---

## 6 · 许可

MIT —— 见 `LICENSE`。

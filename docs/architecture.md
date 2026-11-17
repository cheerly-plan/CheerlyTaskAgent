# CheerlyTaskAgent 架构设计

> 交付链路：**手动新建 / 邮箱 / TAPD 的需求或 bug → 映射到本地仓库 → 本地 CLI agent（codebuddy）改代码 → 结果落本地报告。**
> 目标仓库 `cheerly-plan/CheerlyTaskAgent`（绿地）。本阶段不接大模型，规划交给规则，调度交给确定性代码。

---

## 0. 定位与边界

**它是"工单调度台"，不是"画师"，也不是"规划大脑"。**

| 它做 | 它不做（本阶段） |
|---|---|
| 手动新建（选项目即执行）/ 邮件导入，规则提取标题/类型/git 地址 | 不接 LLM 自动拆任务（后续） |
| git URL 归一化，映射到本地 checkout | 不改 git 历史、不提交、不 push |
| 派发 `codebuddy -p` 在映射仓库执行 | 不回写邮件/TAPD（暂时只本地报告） |
| 幂等、并发、重试、取消、事件流、Markdown 报告 | apply 用 worktree 隔离执行；成功后自动把改动同步到主仓库工作区（不 commit/push，不改 git 历史） |

**环境事实（决定了设计）**
- 本机可用的编码 CLI：`codex`、`@tencent-ai/codebuddy-code`（提供 `codebuddy`，用户指定为执行端）；无 `workbuddy` CLI。
- `CheerlyHarness`（`cheerly`）是流式 chat harness，只有 `now`/`calc` 工具，不能直接改代码；`dsh`（deepseek-harness）是另一套。二者本阶段不接。
- Agent Mail 已连接但未开通，暂用样例邮件/manual；TAPD 无连接器，留接缝。

---

## 1. 总架构

```mermaid
flowchart LR
    subgraph 来源层
      M[手动新建（选项目）] --> S[Source 导入]
      E[邮件] --> S
      T[TAPD 预留接缝] -.-> S
    end
    S --> P[规则解析<br/>标题/类型/git URL]
    P --> W[WorkItem<br/>去重/状态机]
    W --> G[GitProjectRegistry<br/>URL 归一化 → 本地仓库]
    G --> SCH[Scheduler<br/>并发/重试/取消/事件]
    SCH --> EX[CodeBuddyExecutor]
    EX -->|dry-run| CMD[生成提示词+命令<br/>不改动]
    EX -->|apply| WT[隔离 worktree<br/>codebuddy -p]
    WT --> R[Markdown 报告]
    CMD --> R
    SCH -. SSE .-> UI[调度台 UI]
```

## 2. 组件职责

| 组件 | 职责 |
|---|---|
| `sources.py` | `RawItem`（邮件/manual）→ `WorkItem`。规则：标题前缀判 bug/需求、关键词兜底、正则提取 git URL、去签名/引用。TAPD 适配器留注释接缝。 |
| `gitmap.py` | 扫描根目录 git 仓库的 `origin`，`normalize_git_url` 把 HTTPS/SSH/SCP/裸地址统一成 `host/owner/repo`，`resolve()` 映射到 `LocalRepo`。 |
| `models.py` | `WorkItem` 状态机 `discovered→ready→queued→running→succeeded/failed/skipped/cancelled`；`RunResult`、`LocalRepo`。 |
| `scheduler.py` | 导入去重（`source_ref`）、`create_manual`（手动指定项目直接 ready）、映射、派发、幂等、并发上限、退避重试、取消、事件发射。确定性代码，无 LLM。 |
| `executor.py` | `codebuddy -p --output-format json` 非交互执行。`dry-run` 只生成 argv；`apply` 建 git worktree、受限权限、超时、抓 changed_files；成功后 `sync_to_repo()` 以 worktree HEAD 为 base 做三方比较同步回主仓库（主仓库未分叉才覆盖，分叉则尝试干净合并、否则记冲突并备份双方；路径校验/忽略规则）。argv 列表拼接防命令注入。 |
| `report.py` | 每单生成精简 Markdown：概览、执行结果、改动文件、自动同步明细与备份位置、错误；codebuddy 原始 stdout 只提取最终结论、提示词/命令/输出放折叠附录（`<details>`），避免动辄 10 万字符。`render_report` 也供 `/api/items/{id}/report` 实时渲染，老报告一并精简呈现（磁盘 .md 存档不动）。前端轻量 Markdown 渲染器把表格/标题/折叠块结构化展示。 |
| `events.py` / `api.py` | 事件总线 + SSE（沿用 Harness 帧格式）；HTTP 路由。 |
| `web/index.html` | 调度台三 Tab（任务列表 / 项目映射 / 系统设置）：新建任务已合并进任务列表，由右上角「+ 新建任务」按钮打开弹窗（内嵌手动新建 / 邮件导入 / TAPD 预留子 Tab，邮件导入在此切换）；含工作项看板、dry-run/apply/report。 |

## 3. 关键流程

**导入**：邮件 subject/body → `parse_raw`：前缀【BUG】/【需求】优先分类 → 正则提取 git URL → `dedupe_key = source_ref`（邮件 id） → 已存在则跳过（幂等）→ `gitmap.resolve()` 映射本地仓库，成功 = `ready`，失败 = `discovered`（可手动映射）。

**手动新建**：`create_manual(title, description, kind, repo_name)` → 用户显式指定类型与项目 → 项目映射优先解析为 `LocalRepo` → 直接置 `ready`；`dedupe_key` 为随机值，因此同名手动任务不会互相去重。界面三个入口对应：创建草稿（`run_now=false`）、创建并 dry-run、创建并 apply（`approved=true`）。

**执行**：`run(id, exec_mode, model)`。`dry-run` 直接生成命令；`apply` 要求 `approved=true`，建 worktree，`codebuddy -p` 在 worktree cwd 执行，收集 stdout/改动文件。**模型**：`WorkItem.model` 为按任务选择的模型（`build_command` 里 `item.model or executor.model`），新建时可选、卡片上可随时切换；`model=None` 回退全局 `CODEBUDDY_MODEL`。候选由 `config.CODEBUDDY_MODELS`（`codebuddy --help` 真实列表）提供，`TASKAGENT_MODELS` 可收窄。apply 成功后 `sync_to_repo()` 把改动文件**自动同步回主仓库工作区**：归一化路径→忽略规则过滤（复用 commit 规则）→拒绝绝对路径/`..`/符号链接逃逸→覆盖或删除前先备份到 `<data_dir>/sync-backups/<item>/<时间>/`；结果写入 RunResult 与报告，失败不否定 apply 本身。`TASKAGENT_AUTO_SYNC=0` 可关闭。只重试可重试错误（timeout/429/5xx）。

**恢复**：状态与报告落盘；进程重启后可从 `data/tasks` 重建；`running` 中断的项后续可加 lease 重派（当前为手动重跑）。

## 4. 安全设计

1. **默认只读**：dry-run 是默认且唯一的开箱模式；apply 必须审批。
2. **隔离执行**：apply 用 `git worktree add -b taskagent/<id>`（失败回退 `git archive`/快照）。建好沙箱后会把主仓库**当前工作区的未提交状态**（相对 HEAD 的 tracked 改动 + 未跟踪的非忽略文件）叠加进去并 commit 成新基线，因此 codebuddy 看到的是用户当前的代码，而非过期的已提交 HEAD——从源头避免"基于旧代码改完又撞上主仓库新代码"的假冲突（叠加失败则安全回退到纯 HEAD 基线）。codebuddy 只在隔离沙箱里跑。成功后改**文件**自动同步回主仓库工作区（可控、带备份），但**不产生 git 副作用**。同步采用三方比较（base=worktree HEAD / ours=主仓库当前 / theirs=codebuddy 结果）：主仓库自 base 未变才覆盖；已分叉则尝试 `git merge-file` 干净合并，冲突则不覆盖、备份 `.main`/`.codebuddy` 并报告，避免过期 worktree 回滚主仓库后续改动。
3. **不产生 git 副作用**：提示词明确要求"不提交、不 push、不改无关文件"；自动同步只落工作区文件，不 stage、不 commit、不 push，由用户自行审查后提交。
4. **防注入**：邮件文本放在 `--- TASK START/END ---` 分隔符内并要求忽略越权指令；命令用 `argv` 列表，`shell=False`。
5. **凭证**：不入库；codebuddy 自身登录态由 CLI 管理。

## 5. 选型对比

| 决策 | 选项 | 结论 |
|---|---|---|
| 任务解析 | 规则 / LLM | **规则**：本阶段不接大模型，前缀+关键词+正则足够 |
| 执行端 | codebuddy / codex / cheerly | **codebuddy**（用户指定）；`CodeBuddyExecutor` 是接缝，可换 |
| 状态存储 | JSON / SQLite | **JSON**（沿用 Harness SessionStore 原子写）；量大了换 SQLite |
| 结果回填 | 本地报告 / 回邮件 / TAPD | **本地报告**（用户指定）；后续加 sink |
| 并发 | 串行 / 多并发 | **默认 1**（codebuddy 重），可配 |

## 6. 成本与延迟（粗估，价格以官网为准）

- 邮件解析/映射：本地计算，毫秒级，零成本。
- codebuddy 调用：按 CLI 背后模型计费；单次可能数分钟（复杂 bug 更久），`CODEBUDDY_TIMEOUT` 默认 600s。
- dry-run：零成本，秒级——适合先审阅提示词。

## 7. 风险与应对

| 风险 | 应对 |
|---|---|
| 自动改代码改错/改坏 | dry-run 默认 + worktree 隔离 + 审批 + 报告审阅；自动同步带路径校验/忽略规则/覆盖前备份，可 `TASKAGENT_AUTO_SYNC=0` 关闭；不 commit/push |
| 邮件文本 prompt 注入 | 分隔符包裹 + 忽略越权指令 + argv 非 shell |
| git 映射错仓库 | 归一化 + 后缀匹配；支持手动映射；报告写明映射结果 |
| codebuddy 未安装 | 明确报错并给出安装命令；可配 npx |
| 邮箱未开通 | 样例邮件/manual 跑通；EmailSource 接口就绪 |
| 单进程无锁 | 单人使用；并发上限 1；后续加 per-item 锁 |

## 8. 落地状态与下一步

**已完成**：规则解析、git 映射、项目映射配置、新建任务合并进任务列表（按钮 + 弹窗，选项目即可 dry-run/apply）、去重、dry-run/apply 执行器、worktree、报告、SSE、UI、测试全绿、demo 闭环跑通。

**下一步（按优先级）**：
1. 开通 Agent Mail，接真实 `ListMessages`/`SearchMessages` → `/api/import/emails`。
2. 装 codebuddy CLI，先用 dry-run 审提示词，再对低风险仓库小范围 apply。
3. TAPD source 适配器（API 凭据从环境变量读）。
4. 结果回填（邮件回复 / TAPD 状态）。
5. 可选：接 LLM 做需求拆解与补丁审阅。

## 9. 待确认

- 邮箱开通后，按哪些条件拉邮件（未读 / 关键词 / 发件人白名单）？
- 真实 apply 时允许 codebuddy 用哪些工具（Bash 是否放开）？
- 报告是否需要汇总成每日摘要？

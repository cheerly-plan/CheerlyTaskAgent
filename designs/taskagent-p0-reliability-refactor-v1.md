# CheerlyTaskAgent P0 可靠性重构评估报告

> 设计台账：2026-11-24 · v1 · 轨道=重构 · 用户决策=体感与可靠性优先 / 只做 P0 急修 / 严格兼容 · 审计材料=28 个源码模块 + 17 个测试文件通读 + docs + README · 独立评审：**存疑 2 项已按复审结论回修并用真实生命周期测试验证，复审额度（1 轮）已用尽，残留风险见 §5**。

## 0. 用户已确认的决策 vs 默认假设

- **用户已确认**：① 优化目标=体感与可靠性优先；② 交付形态=只做 P0 急修（不碰大结构）；③ 变更约束=严格兼容（对外行为 / HTTP 接口 / 配置格式不变，测试全绿，每步可回滚）。
- **本次默认假设（可推翻）**：P0 范围按审计的缺口定级划为「确定性故障与安全隐患」，不纳入可维护性和成本类优化（God Object 拆分、dialect 重复实现收敛、LLM 规划缓存留待后续版本）。`RawItem.from_email` / `SourceType.EMAIL` 虽属历史残留，但被 20+ 测试当作通用构造器且承担旧数据反序列化兼容，判定为保留。

## 1. 现状映射表

| 维度 | 现状实现 | 备注 |
|---|---|---|
| E 执行循环 | `scheduler.py` 状态机 + 三层锁 + 重试退避 + 取消标志；`automation.py` 双触发点 | 并发设计扎实；可中断性弱（本次修复） |
| T 工具注册 | `executor.py` 抽象 + CodeBuddy/DSH 两实现 + Registry；`dialect.py` 策略库 | 抽象与实现脱节（未接线 ModelCatalog，非本次范围） |
| C 上下文管理 | `build_prompt` / `planner.py` / `repoprofile.py` / `route.py` | 两条选仓库机制并存（非本次范围） |
| S 状态存储 | `store.py` 原子写、`models.py` 手工序列化、`_seen_keys` 幂等索引 | `_seen_keys` 截断（本次修复） |
| L 生命周期/安全 | `tapd.py` 回填、`commitfilter.py`、`_auto_merge` / `commit` 硬编码副作用 | 无统一钩子协议（非本次范围） |
| V 评估接口 | `verification.py` 完整门禁 + `verifyconfig.py` 自动探测 | 工程质量和安全设计最突出，本次不动 |
| 范式 P | 配置驱动后端（声明式）+ 单机中心化调度 + CLI 子进程执行 | 保持 |
| 模型组合现状 | 两套模型体系解耦（执行 CLI vs 后端 Agent LLM）；按任务选模型 | `plan_repo` 无缓存无计量（成本类，非本次范围） |
| 框架外现状 | TAPD 数据源、Web UI、worktree 三方合并 | 保持 |

## 2. 缺口清单（本次处理范围）

| # | 缺口 | 定级 | 判据来源 | 证据 |
|---|---|---|---|---|
| 1 | CLI 子进程执行中不可取消；cancel 在子进程仍写 worktree 时删目录；shutdown 只 cancel task 不留进程句柄 | 风险 | E 层必答 9 / 六层通用坑 | `executor._run_apply` 原 `wait_for(communicate)` 无轮询；`scheduler.cancel` 直接 `_drop_worktree`；`shutdown` 仅 `task.cancel()` |
| 2 | `_seen_keys` 只加载前 2000 条历史，超限后旧幂等键丢失→重复建单 | 风险 | S 层必答 4 / 生产级清单 | `scheduler.py:99` 原 `store.list(limit=2000)` |
| 3 | `cli.main()` 手工重建 Settings 漏 7 字段，scan/demo 下静默失效 | 风险 | 配置单一来源 | `cli.py:163-173` 原 9 字段赋值 vs Settings 16+ 字段 |
| 4 | `_create_worktree` 见内容即复用，无 HEAD 的半构建目录被当基线 | 优化 | S 层证据版本绑定 | `executor.py:1002-1003` 原 `if _dir_has_content: return True` |
| 5 | `agents.py` 205 行引用不存在的类型，全库零调用 | 优化 | 死代码 | `agents.py:22` `from .models import Artifact, Task, TaskKind`（模型中无此类），grep 全仓零 import |

## 3. 优先级与方案（重构决策卡）

**改什么（按级别排序）**：风险 1→2→3，优化 4→5。均已完成，见 §4 变更清单。

**不改什么（及理由）**：
- 三个 God Object（`api.create_app` 1164 行 / `Scheduler` 1000+ 行 / `CodeBuddyExecutor` 1400+ 行）：拆分属 P1 结构性改动，超出「只做 P0 急修」范围，且严格兼容下收益/风险比不划算。
- `dialect.ModelCatalog` 未接线 + `executor` 模型发现重复实现：属「做了一半的重构」，清理需回归全部模型探测路径，非 P0。
- LLM 规划缓存、`_tapd_last_sync` 进程内游标持久化：成本类，非可靠性。
- `RawItem.from_email` / `SourceType.EMAIL`：兼容构造器 + 旧数据反序列化依赖，删除会破坏严格兼容。

**迁移顺序**：① 进程注册表与信号（executor）→ ② scheduler cancel/shutdown 协议 → ③ 幂等索引 → ④ CLI Settings → ⑤ worktree HEAD 守卫 → ⑥ 死代码删除 → ⑦ 测试补齐。每步独立可验（pytest 子集），无跨步数据迁移。

**回滚点**：全部改动在 git 工作树中，逐文件 `git checkout -- <path>` 可独立回滚；`agents.py` 的删除已 staged，`git restore --staged --worktree src/taskagent/agents.py` 可完整恢复。

## 4. 变更清单（可回滚单元）

| 文件 | 改动 | 回滚方式 |
|---|---|---|
| `src/taskagent/executor.py` | Executor 基类新增 `_live_procs` 注册表、`cancel_process`（POSIX 进程组 SIGTERM + 延迟 SIGKILL 升级）、`snapshot_live_processes` / `hard_kill_snapshot` / `cancel_all_processes`；`_run_apply` 改为 ≤200ms 轮询取消/超时并绑定进程句柄；`_create_worktree` 要求目标自身是 git 根且有 HEAD 才复用 | `git checkout --` 单文件 |
| `src/taskagent/scheduler.py` | `cancel()` 先 signal 活进程、无活进程才即时删 worktree；`_execute` 取消分支在 run 返回后安全回收 worktree；`shutdown()` 先快照进程→SIGTERM→cancel tasks→从快照硬杀顽固进程 | `git checkout --` 单文件 |
| `src/taskagent/cli.py` | `main()` 用 `dataclasses.replace(base, host=…, port=…)` 替代手工字段赋值 | `git checkout --` 单文件 |
| `src/taskagent/__init__.py` | 修正过时的包文档字符串 | `git checkout --` 单文件 |
| `src/taskagent/agents.py` | 删除（git rm，205 行死代码） | `git restore --staged --worktree` |
| `tests/test_p0_reliability.py` | 新增 8 个离线回归测试 | 删除文件 |

验证基线：pytest 261 → **269 passed**（新增 8，零回归）；ruff `All checks passed`；`compileall` 通过。唯一 warning 是 Starlette 的 httpx 弃用提示，与本次改动无关。

## 5. 风险与回滚

| 改动 | 风险 | 回滚方式 |
|---|---|---|
| 进程组 SIGTERM + SIGKILL 升级 | 中：若 CLI 有未落盘的中间产物，SIGTERM 可能来不及优雅收尾；已用 2s grace + 快照硬杀兜底 | 单文件 checkout |
| `_spawn_loop` 跨事件循环复用 | 低：`is_closed()` 检查后降级到当前 running loop，已覆盖 | 单文件 checkout |
| `_seen_keys` 全量加载 | 低：10 万+ 任务历史会拖慢启动、升内存；正确性无问题，规模超阈值时再改分页索引 | 单文件 checkout |
| worktree HEAD 守卫 | 低：linked worktree `.git` 为文件时 `rev-parse --show-toplevel` 返回自身路径，复审已核实不误重建 | 单文件 checkout |

**独立评审记录**：起独立上下文子智能体对缺口定级与决策卡做两轮评审（四要素：独立上下文 / 不看执行者推理 / 显式标准 / 迭代预算 1 轮）。
- 第 1 轮：缺口 2/3/4/5 通过；缺口 1 存疑——shutdown 快速退出丢失 SIGKILL 升级、stale unbind 竞态、测试假阳性。
- 回修：捕获 spawn loop、unbind 身份校验、shutdown 硬杀兜底、强化测试。
- 复审（额度已用尽）：`stale unbind` 确认修复；发现硬杀兜底因 finally 先 unbind 而空转。已二次回修为「取消前快照进程句柄 + 闭包持有进程对象的 SIGKILL 升级 + 从快照硬杀」，并用模拟真实生命周期（`_run_apply` finally unbind 后仍杀顽固子进程）的测试验证通过。**该最终回修未再经独立复审（迭代预算上限）**，作为残留风险如实标注。

## 6. 遗留问题与未知区

- **未覆盖的 P1/P2**（明确不在本次范围）：God Object 拆分、dialect 未接线抽象清理、LLM 规划缓存与 token 计量、TAPD 游标持久化、L 层统一钩子协议、`store.py`/`cli.py`/`models.py` 专门测试、cancel 与运行中写操作的更深竞态（已在进程级兜底，文件级原子性未提供）。
- **未在真机复现**：POSIX 进程组信号在 Linux CI 上的行为与 macOS 一致（`start_new_session` + `killpg` 为 POSIX 标准），本次仅在 macOS 验证。
- **最终回修未经独立复审**：见 §5 评审记录，按方法论迭代预算上限处理。
- **主观验收边界**：「体感提升」无量化基线（无生产墙钟/取消延迟数据），本次以「取消不超过 200ms 轮询间隔响应」为可测替代指标。

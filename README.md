# CheerlyTaskAgent（需求调度台）

**手动新建 / 邮箱 / TAPD 里的需求或 bug → 映射本地仓库 → 调 `codebuddy` CLI 改代码 → 结果落本地报告。**

本阶段不接大模型：邮件解析、git 映射都是规则式的；调度是确定性代码。默认 `dry-run`——只生成要执行的提示词和命令，**不改动任何仓库**；`apply` 模式在隔离 worktree 里真实执行，需显式审批，不提交不推送。

## 快速开始

```sh
uv sync
uv run taskagent scan      # 扫描本地 git 仓库（~/.Desktop/code/projects）
uv run taskagent demo      # 用 3 封样例邮件跑通 邮件→映射→执行→报告
uv run taskagent serve     # http://127.0.0.1:8010 打开调度台
# 在界面左侧「新建执行（手动）」选项目、写需求，点「创建并 dry-run」即可；
# 确认提示词无误后再点工作项上的 apply（或在创建时直接点「创建并 apply」）。
```

## 核心闭环

```
手动输入 / 样例邮件 / TAPD
   │ 规则解析：标题、bug/需求分类、提取 git 地址
   ▼
WorkItem ──git 归一化──▶ 本地仓库（HTTPS/SSH 格式统一匹配）
   │ 调度（幂等/并发/重试/取消）
   ▼
codebuddy -p  (dry-run 只生成命令 / apply 在 worktree 执行)
   ▼
data/reports/<id>.md  ← 状态、提示词、命令、输出、改动文件
```

## 关键能力

- **任务来源（手动优先）**：界面「新建执行（手动）」可直接选项目映射、写标题/描述、选 bug/需求/任务，支持「创建草稿」「创建并 dry-run」「创建并 apply」三种方式；不依赖邮件或 TAPD。`email` 导入保留为独立入口，`tapd` 接口已留接缝（`sources.py`）。真实邮箱需先开通 Agent Mail，再用 `/api/import/emails` 把 `ListMessages` 结果喂进来。
- **按任务选模型**：新建任务时可选本次 codebuddy 用的模型（默认「跟随全局」）；工作项卡片上也能随时切换，dry-run/apply 时按该选择下发 `--model`。候选来自 `codebuddy --help` 的真实模型（18 个，含 `fast-model`/`balanced-model`/`deep-model`、`hy4std/hy4flash`、`deepseek-v4.1-flash`/`deepseek-v4-pro`、`glm-5.3` 等），可用 `TASKAGENT_MODELS` 收窄候选；全局默认由 `CODEBUDDY_MODEL` 指定。报告里记录每个任务实际用的模型。
- **项目映射配置（推荐）**：维护一张「项目名/别名 → 本地文件夹」列表，邮件标题/正文出现任一别名即命中，**不依赖 git**。列表默认为空（`data/projects.json`），**完全由你手动添加**——可在调度台逐级点选文件夹、或扫描一个父目录批量勾选加入；也可手工填写。增删改、启停即时生效。匹配规则：中文按子串、英文按词边界、多个命中取最长别名、可停用。
  - **选择文件夹**：点「选择文件夹」→ 从主目录逐级点进子文件夹 → 「选择当前文件夹」→ 自动带出项目名与路径，确认即建映射。
  - **扫描文件夹**：点「扫描文件夹」→ 选一个父目录（如 `~/Desktop/code/projects`）→ 后端列出其下 2 层子文件夹（git 仓库排前并标注 origin）→ 勾选后批量加入。
  - 目录浏览是**只读且限制在主目录内**（可用 `TASKAGENT_FS_ROOT` 改边界），不会改动文件系统。
- **git 映射（回退）**：扫描本地仓库 `origin`，归一化 `https://` / `git@` / `ssh://` / 裸 `host/owner/repo` 统一匹配；项目映射未命中时才走 git 扫描，也可在界面手动指定仓库。
- **幂等**：`source_ref`（邮件 message id / TAPD id）做去重键，同一封邮件重复导入不会重复建单。
- **安全**：默认 dry-run；apply 用 `git worktree` 隔离 + 显式审批 + 提示词注入防护；命令用 argv 列表拼接（非 shell），邮件文本无法注入 shell 命令。
- **自动同步**：apply 成功后，codebuddy 的改动文件自动同步回主仓库工作区（无需手动从 worktree 拷贝），看板即显示「完成（已同步 N 个文件）」。同步只碰改动文件、按忽略规则过滤（`.env`/`data/`/venv 等不同步）、拒绝路径逃逸、覆盖/删除前先备份到 `<data_dir>/sync-backups/<item>/<时间>/`；失败会写进报告但不否定 apply 结果。用 `TASKAGENT_AUTO_SYNC=0` 可关闭，回到纯隔离模式。
- **可观测**：SSE 实时推送导入/映射/排队/执行/进度/成功/失败/重试；每单都有 Markdown 报告（含自动同步明细与备份位置）。

## 界面

- 左侧：
  1. **新建执行（手动）**（一级入口）：目标项目下拉来自映射配置，选类型、写标题/描述，三个按钮分别是创建草稿 / 创建并 dry-run / 创建并 apply。
  2. **从邮件导入**：贴邮件主题/正文，自动识别分类与 git 地址，导入后仍按看板流程执行。
  3. **项目映射配置**：「选择文件夹」逐级点选 / 「扫描文件夹」批量勾选 / 手动新增；编辑表单的路径框也带「选择」按钮。界面不再展示 git 扫描备选列表。
- 右侧：工作项看板。未映射的可选仓库映射；已映射的可跑 `dry-run` 或 `apply`（需确认）；完成后点「查看报告」。

## 配置

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `TASKAGENT_PROJECT_ROOTS` | `~/Desktop/code/projects` | 扫描本地 git 仓库的根目录（多个用 `:` 分隔） |
| `TASKAGENT_DATA_DIR` | `data` | 任务/报告/worktree 目录 |
| `TASKAGENT_HOST` / `TASKAGENT_PORT` | `127.0.0.1` / `8010` | 监听地址 |
| `CODEBUDDY_COMMAND` | `codebuddy` | 执行 CLI（本机未装可用 `npx -y @tencent-ai/codebuddy-code@latest`） |
| `CODEBUDDY_MODEL` | 空 | 全局默认模型（`--model`）；任务可单独覆盖 |
| `TASKAGENT_MODELS` | 空（用全部 18 个） | 允许选择的模型 id，逗号分隔，用于收窄下拉候选 |
| `CODEBUDDY_TIMEOUT` | `600` | 单次执行超时秒数 |
| `TASKAGENT_MAX_CONCURRENT` | `1` | 并发执行数（codebuddy 较重，默认串行） |
| `TASKAGENT_PROJECT_MAP_FILE` | `<data_dir>/projects.json` | 项目映射列表 JSON 路径；默认空列表，由界面手动维护 |
| `TASKAGENT_FS_ROOT` | `~`（主目录） | 文件夹浏览器/扫描器的只读边界，防止越界浏览 |
| `TASKAGENT_AUTO_SYNC` | `1`（开启） | apply 成功后是否自动把改动同步回主仓库工作区；设 `0` 关闭 |

> 本机 `codebuddy` 尚未安装到 PATH。安装：`npm i -g @tencent-ai/codebuddy-code`；或设 `CODEBUDDY_COMMAND="npx -y @tencent-ai/codebuddy-code@latest"`（注意 npx 首启可能拉包）。

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 调度台 UI |
| `GET` | `/api/config` | 配置概览 |
| `GET` | `/api/fs/browse?path=` | 只读浏览目录（返回真实绝对路径，限制在 `TASKAGENT_FS_ROOT` 内） |
| `POST` | `/api/fs/scan` | 扫描目录 `{path,depth}` 返回候选项目（git 仓库优先） |
| `GET` | `/api/mappings` | 项目映射配置列表 |
| `POST` | `/api/mappings` | 新增项目映射 `{name,aliases,path,enabled,note}` |
| `POST` | `/api/mappings/bulk` | 批量新增 `{items:[...]}`，返回 added/failed |
| `PUT` | `/api/mappings/{id}` | 编辑/启停（支持局部字段） |
| `DELETE` | `/api/mappings/{id}` | 删除项目映射 |
| `GET` | `/api/items` | 工作项列表 |
| `POST` | `/api/import` | 导入单个工作项（邮件/手动） |
| `POST` | `/api/tasks/manual` | 手动新建并可立即执行：`{title,description,kind,repo_name,exec_mode,run_now,approved}` |
| `POST` | `/api/import/emails` | 批量导入邮件（`messages` 来自邮箱工具） |
| `POST` | `/api/items/{id}/map` | 手动映射仓库 |
| `POST` | `/api/items/{id}/run` | 执行 `{"exec_mode":"dry-run|apply","approved":true}` |
| `POST` | `/api/items/{id}/cancel` | 取消 |
| `GET` | `/api/items/{id}/report` | Markdown 报告 |
| `GET` | `/api/events` | SSE 事件流 |

## 代码结构

```
src/taskagent/
├── config.py      环境变量配置
├── models.py      WorkItem / 状态机 / RunResult / LocalRepo
├── gitmap.py       git URL 归一化 + 本地仓库扫描映射（回退）
├── projectmap.py   项目名/别名 → 本地文件夹映射（存储 + 文本匹配 + CRUD）
├── fsmap.py        只读目录浏览 + 按需项目扫描（带路径边界，供界面选文件夹）
├── projects.seed.json  映射种子（默认空，列表由界面手动维护）
├── sources.py      邮件/手动解析（+ TAPD 接缝）
├── executor.py     codebuddy CLI 执行器 + dry-run/worktree 安全策略
├── scheduler.py    导入/映射/调度/幂等/重试/取消
├── report.py       Markdown 报告生成
├── store.py        JSON 原子写持久化
├── events.py       事件总线 + SSE
├── api.py / cli.py
└── web/index.html  调度台 UI
```

## 开发

```sh
uv run pytest
uv run ruff check src tests
```

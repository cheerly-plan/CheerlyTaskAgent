# CheerlyTaskAgent（需求调度台）

**手动新建 / TAPD 里的需求或 bug → 映射本地仓库 → 按任务选择执行后端（CodeBuddy / DSH CLI）改代码 → 结果落本地报告。**

本阶段不接大模型：git 映射是规则式的；调度是确定性代码。默认 `dry-run`——只生成要执行的提示词和命令，**不改动任何仓库**；`apply` 模式在隔离 worktree 里真实执行，需显式审批，不提交不推送。

## 快速开始

```sh
uv sync
uv run taskagent web-build # 构建前端（首次运行 / 每次改前端后；需要 Node 18+ 与 pnpm）
uv run taskagent scan      # 扫描本地 git 仓库（~/.Desktop/code/projects）
uv run taskagent demo      # 用 3 条样例需求跑通 建单→映射→执行→报告
uv run taskagent serve     # http://127.0.0.1:8010 
uv run taskagent serve + cd web && pnpm dev --host 127.0.0.1
# 点任务列表右上角「+ 新建任务」选项目、写需求，点「创建并 dry-run」即可；
# 确认提示词无误后再点工作项上的 apply（或在创建时直接点「创建并 apply」）。
```

## 核心闭环

```
手动输入 / TAPD
   │ 规则解析：标题、bug/需求分类、提取 git 地址
   ▼
WorkItem ──git 归一化──▶ 本地仓库（HTTPS/SSH 格式统一匹配）
   │ 调度（幂等/并发/重试/取消）
   ▼
执行后端 -p  (codebuddy / dsh，按任务选择；dry-run 只生成命令 / apply 在 worktree 执行)
   ▼
data/reports/<id>.md  ← 状态、提示词、命令、输出、改动文件
```

## 关键能力

- **任务来源**：「+ 新建任务」弹窗做手动录入；外部数据源**只有 TAPD**（内部工单系统同理），Bug / 需求在这里维护，有状态机、优先级、责任人、评论、附件。邮件接入已移除——非结构化、无状态机、无法回填，不适合做需求来源。
  - **数据源（单一对象，当前唯一类型为 TAPD）**：在「数据源」Tab 的卡片顶部用下拉选数据源类型（目前仅 TAPD），下方是 TAPD 配置表单——填 API 账号 / 秘钥（Basic Auth，即应用 ID / 秘钥）、API 地址（公网 `api.tapd.cn` / 内网 `apiv2.tapd.woa.com`）。**项目（key 仍是 workspace_id，支持多选、逗号分隔）**：点「拉取」弹窗里**企业 ID 用下拉选择**（选项从已记录项目的 company_id 提取，也可手动输入新企业）→ 打开弹窗或切换企业即自动拉取项目，也可手动点「拉取」刷新 → **多选项目**后点「应用」写回 workspace_id，并可在同一弹窗里为每个项目勾选其管理的本地仓库；企业 ID 与项目列表会存进 `tapd.projects` 缓存，下次打开弹窗无需拉取也能看到已填项目；页面「项目」下拉只读展示项目名及其关联的本地仓库，「新增」弹窗登记项目名 + 项目 ID。企业自身（category=organization）已从候选中过滤，不会被当成项目。接入方式为**定时拉取**（独立开关 `auto_pull`，默认关闭）——开启后按间隔（默认 5 分钟）增量拉取需求 / 缺陷（基于 modified 时间）；Webhook 表单已隐藏（后端 `/api/tapd/webhook` 接口仍保留）。按 `tapd:<type>:<id>` 幂等建单，执行完成后把状态回填到 TAPD（需写权限）并把报告路径写成评论。也可点「立即同步」手动拉一次。配置存 `data/settings.json`，即时生效。
  - **TAPD 项目 ↔ 本地仓库（多对多关联）**：数据源页「项目」输入框显示项目名称（只读），可点「新增」用弹窗登记「项目名称 + 项目 ID」并勾选它管理的本地仓库（无需填企业 ID）；点「拉取项目」后弹窗里多选项目，并在**每个项目下面单独勾选它管理的本地仓库**，一眼分清谁管谁；也可在「项目映射」页给某个仓库勾选多个 TAPD 项目（项目显示名称，不再是裸 ID），两处配置同一份数据。拉取需求/缺陷时：只关联一个仓库就直接归到它；关联多个则**不做文本映射猜测**，工单先保持待分配并列出候选仓库，留给后续按需求/代码内容选择执行仓库（可引入 LLM 按顺序执行）；没有关联时仍按原规则文本匹配。
  - 旧的 `/api/import` 端点保留供程序化调用/兼容。
- **按任务选模型**：新建任务时可选本次执行的模型（默认「跟随后端默认」）；工作项上也随时可切，dry-run/apply 时按该选择下发 `--model`。候选由**当前后端的 CLI 实测发现**：codebuddy 走 `--help` 里的 `--model` 列表（含 `hy4std/hy4flash`、`deepseek-v4.1-flash`/`deepseek-v4-pro`、`glm-5.3` 等），可用 `TASKAGENT_MODELS` 收窄候选；全局默认由后端 profile 的 `model` 字段指定。报告里记录每个任务实际用的模型。
  - **内置 vs 自定义**：CLI 自带目录里的模型与用户自己配置的模型（codebuddy 的 `models.json`、DSH 手写网关路由）由后端标记 `custom` 区分，下拉里加「（自定义）」后缀，系统设置里显示「内置 N · 自定义 M」统计。
    - **codebuddy 自定义模型**：读取 `~/.codebuddy/models.json`（`CODEBUDDY_CONFIG_DIR` / `WORKBUDDY_CONFIG_DIR` 可改目录），兼容 `{"models": [...]}` 与 `{"buffer_id": {...}}` 两种写法，追加在内置模型之后；与服务端目录同 id 时以本地定义为准。
    - **DSH 自定义模型**：`llm-pi-ai.providers` 里不在内置 catalog 中的路由即为自定义（覆盖层的 patch 会为其补上 `api: openai-completions`）；若该路由的模型只在网关上声明，会额外请求 `GET {baseURL}/models` 列出（`<路由>/<模型>`，可用 `TASKAGENT_DSH_DISCOVERY_ROUTE` 指定路由）。
  - **DSH 协议差异**：启动命令是 `dsh --profile headless`，headless 应用用 `--json`（NDJSON 事件流）而不是 `--output-format json`，权限预设有 `read-only / workspace-write / danger-full-access` 三档（`acceptEdits` 自动映射到 `workspace-write`），且 launcher 参数（`--profile` / `--patch`）必须排在应用参数之前。
  - **DSH 模型选择**：`dsh --profile headless` 没有 `--model` 选项，模型钉在 profile 的 `agent-default-model` 里；但它接受 `--patch <文件>`，所以调度台把「本次用哪个模型」写成一个临时 patch（`agent-default-model`，必要时连同 `llm-pi-ai.providers` 的网关定义），随命令下发，跑完即删。
    - 模型清单从 profile 层读取：`~/.dsh/profiles/*/cordis.patch.yml` 里 `llm-pi-ai.providers.<provider>.models[]`，因此界面显示的是人类可读名称（DeepSeek V4 Pro / Kimi K2.7 Code / Qwen3.8 Max …）。
    - **并发安全**：每个任务一个独立 patch 文件，不写你的 profile，互不覆盖。
    - **凭据**：走网关的模型需要对应的 API Key。headless 只读环境变量，因此执行器会自动读取 DSH 自己的凭据库 `$DSH_HOME/.credentials.yaml` 的 `refs` 并注入子进程环境（显式导出的同名变量优先）；`DSH_HOME` 与这些变量都会透传。
    - **探测时机**：能力验证（`--patch` 可用 + 条目可覆盖 + 叠加生效）**不在页面加载时做**，而是第一次展开该后端的模型下拉时调 `POST /api/backends/{id}/probe`，结果缓存并回写界面；不满足就当场降级为只读。换机器/换版本不会因为一次探测失败而污染启动。
    - **可移植性**：模型目录来自本机 profile 配置，换机器能否显示取决于对方的 DSH 配置——装了 DSH 客户端并声明过 provider 就和你一样；只用官方账号登录时官方模型由服务端下发、本地拿不到目录，界面会如实退化为「由必选模型/由 CLI 决定」。DSH 选择能力是**实测探测**的（`--patch` 可用 + `agent-default-model` 可覆盖 + 叠加后确实生效），任一条不满足就自动降级为只读，绝不承诺做不到的选择；日志里若出现 `patch: entry ... not found`，该次任务会被判为失败而不是「成功但跑错模型」。
- **项目映射配置（推荐）**：维护一张「项目名/别名 → 本地文件夹」列表，任务标题/正文出现任一别名即命中，**不依赖 git**；每条映射还可填「关联 TAPD 项目 ID」（workspace_id），把某个 TAPD 项目直接绑定到该本地仓库。列表默认为空（`data/projects.json`），**完全由你手动添加**——可在调度台逐级点选文件夹、或扫描一个父目录批量勾选加入；也可手工填写。增删改、启停即时生效。匹配规则：中文按子串、英文按词边界、多个命中取最长别名、可停用；TAPD 绑定优先于文本匹配。
  - **选择文件夹**：点「选择文件夹」→ 从主目录逐级点进子文件夹 → 「选择当前文件夹」→ 自动带出项目名与路径，确认即建映射。
  - **扫描文件夹**：点「扫描文件夹」→ 选一个父目录（如 `~/Desktop/code/projects`）→ 后端列出其下 2 层子文件夹（git 仓库排前并标注 origin）→ 勾选后批量加入。
  - 目录浏览是**只读且限制在主目录内**（可用 `TASKAGENT_FS_ROOT` 改边界），不会改动文件系统。
- **git 映射（回退）**：扫描本地仓库 `origin`，归一化 `https://` / `git@` / `ssh://` / 裸 `host/owner/repo` 统一匹配；项目映射未命中时才走 git 扫描，也可在界面手动指定仓库。
- **幂等**：`source_ref`（`tapd:<type>:<id>`）做去重键，同一个 TAPD 工单重复同步不会重复建单。
- **安全**：默认 dry-run；apply 用 `git worktree` 隔离 + 显式审批 + 提示词注入防护；命令用 argv 列表拼接（非 shell），任务文本无法注入 shell 命令。
- **自动同步（三方比较，防覆盖）**：apply 先建隔离 worktree，并把主仓库当前工作区的未提交改动叠加进去作为基线，所以 codebuddy 改的是你眼前的最新代码，而不是过期的已提交版本（叠加失败会安全回退到纯 HEAD 基线）。apply 成功后，codebuddy 的改动文件自动同步回主仓库工作区（无需手动从 worktree 拷贝），看板即显示「完成（已同步 N 个文件）」。对每个文件以 worktree 的 HEAD 为 base，与主仓库当前版本、codebuddy 结果做三方比较：**仅当主仓库自 base 以来未改动才直接覆盖**；若主仓库也已改动，先尝试三方合并（`git merge-file`，仅在无冲突时写入），否则**不覆盖**，把双方版本备份为 `.main` / `.codebuddy` 并在报告中列出冲突，看板显示「N 个文件冲突未覆盖」。这样过期 worktree 不会回滚主仓库里更新的改动。同步只碰改动文件、按忽略规则过滤（`.env`/`data/`/venv 等不同步）、拒绝路径逃逸；备份在 `<data_dir>/sync-backups/<item>/<时间>/`；失败写进报告但不否定 apply 结果。用 `TASKAGENT_AUTO_SYNC=0` 可关闭，回到纯隔离模式。
- **可观测**：SSE 实时推送导入/映射/排队/执行/进度/成功/失败/重试；每单都有 Markdown 报告（含自动同步明细与备份位置）。

## 界面

前端是 `web/` 下的独立工程：**Vite + React 18 + TypeScript + Tailwind CSS + shadcn/ui**（组件由 shadcn CLI 生成并纳入源码，按需增删）。构建产物输出到 `src/taskagent/web/` 并由 FastAPI 托管，**该目录不入仓**，所以首次运行前要执行一次 `uv run taskagent web-build`（等价于在 `web/` 里 `pnpm install && pnpm build`）；没构建时后端会返回一页带命令的引导，而不是空白 503。

- 前端热更新：后端先 `uv run taskagent serve`，再 `cd web && pnpm dev --host 127.0.0.1`（`/api` 与 SSE 自动代理到 `127.0.0.1:8010`），打开 `http://127.0.0.1:5173`。
- 加组件：`cd web && pnpm dlx shadcn@latest add <component>`，组件落在 `web/src/components/ui/`。
- 类型检查：`cd web && pnpm typecheck`。

顶部 Tab 划分四个主页面，新建任务已合并进任务列表（按钮 + 弹窗）：

1. **任务列表**：右上角「+ 新建任务」按钮打开**纯手动**录入弹窗；外部来源（TAPD）在「数据源」Tab 接入。列表默认表格视图，按来源 / 状态 / 项目筛选；**点击表头**即可按标题 / 类型 / 来源 / 项目 / 状态 / 创建时间升序或降序（再次点击切换方向，卡片视图用工具栏的「排序」下拉 + 方向按钮）；点击条目查看报告（无报告时显示工单详情），配套重试、apply、取消、删除、按任务切换模型等操作，可切换卡片视图。
2. **项目映射**：以配置表格统一管理项目名 / 别名 / 本地路径 / 仓库信息，支持「选择文件夹」逐级点选、「扫描文件夹」批量勾选与手动新增、编辑、启停、删除。
3. **数据源**：唯一的外部来源接入中心，即 **TAPD 卡**——配置 API 凭证 / 项目（手填多个 ID 或从拉取的项目列表多选）/ 关联的本地项目 / Webhook 地址与 secret / 拉取类型 / 回填状态 / 定时开关，提供「拉取项目」「立即同步」。凭证明文保存在本机 `data/settings.json`，密码留空表示保持原值；配置即时生效，无需重启。
4. **系统设置**：「执行后端」完全由配置驱动——可自由增删后端，每个后端含名称、启用开关、CLI 命令/路径、默认模型、超时、执行后同步；没有后端时任务会因缺少执行器而失败。「调度策略」设定新建任务的默认后端（同时也是缺失/禁用后端的运行时兜底）、并发数与自动同步。新装默认种入 CodeBuddy CLI / DSH 两个普通种子，均可删除、可禁用，无特权。后端都遵循同一非交互 CLI 协议（`-p --permission-mode`），建任务时可选任一已启用后端，任务按自身 `executor` 字段分派。部分参数（如 CLI 路径）保存后需重启服务生效。

## 配置

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `TASKAGENT_PROJECT_ROOTS` | `~/Desktop/code/projects` | 扫描本地 git 仓库的根目录（多个用 `:` 分隔） |
| `TASKAGENT_DATA_DIR` | `data` | 任务/报告/worktree 目录 |
| `TASKAGENT_HOST` / `TASKAGENT_PORT` | `127.0.0.1` / `8010` | 监听地址 |
| `CODEBUDDY_COMMAND` | `codebuddy` | 执行 CLI（本机未装可用 `npx -y @tencent-ai/codebuddy-code@latest`） |
| `CODEBUDDY_MODEL` | 空 | 默认执行后端的默认模型（`--model`）；任务可单独覆盖 |
| `TASKAGENT_MODELS` | 空（用后端动态探测的全部） | 允许选择的模型 id，逗号分隔，用于收窄下拉候选 |
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
| `GET` / `PUT` | `/api/settings` | 读取 / 保存系统设置（CLI 路径、DSH、模型、TAPD） |
| `POST` | `/api/tapd/workspaces` | 按企业 ID 拉取企业下项目列表 `{company_id}`（回退到已保存配置），供选 workspace_id；企业自身 category=organization 已过滤 |
| `POST` | `/api/tapd/sync` | 立即从 TAPD 增量拉取需求 / 缺陷并幂等建单（可选 `modified_since`） |
| `POST` | `/api/tapd/webhook` | 接收 TAPD Webhook（json/form），校验 secret 后按 event+id 回源拉详情建单；delete 事件忽略 |
| `GET` | `/api/fs/browse?path=` | 只读浏览目录（返回真实绝对路径，限制在 `TASKAGENT_FS_ROOT` 内） |
| `POST` | `/api/fs/scan` | 扫描目录 `{path,depth}` 返回候选项目（git 仓库优先） |
| `GET` | `/api/mappings` | 项目映射配置列表 |
| `POST` | `/api/mappings` | 新增项目映射 `{name,aliases,path,enabled,note,tapd_workspace_id}` |
| `POST` | `/api/mappings/bulk` | 批量新增 `{items:[...]}`，返回 added/failed |
| `PUT` | `/api/mappings/{id}` | 编辑/启停（支持局部字段） |
| `DELETE` | `/api/mappings/{id}` | 删除项目映射 |
| `GET` | `/api/items` | 工作项列表 |
| `POST` | `/api/import` | 导入单个工作项（手动） |
| `POST` | `/api/tasks/manual` | 手动新建并可立即执行：`{title,description,kind,repo_name,exec_mode,run_now,approved}` |
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
├── sources.py      TAPD/手动 → RawItem 解析
├── tapd.py         TAPD Open API 客户端：Basic Auth 拉取需求/缺陷 + 状态回填/评论
├── executor.py     Executor 统一接口 + 两类 CLI 执行器（codebuddy/dsh）+ dry-run/worktree 安全策略
├── backends.py     依据 UI 配置构建 ExecutorRegistry（每类一套配置）
├── scheduler.py    导入/映射/调度/幂等/重试/取消，按任务 executor 字段分派后端
├── report.py       Markdown 报告生成
├── store.py        JSON 原子写持久化
├── events.py       事件总线 + SSE
├── api.py / cli.py
└── web/            构建产物（Vite 输出，不入仓）
```

```
web/                    前端工程源码
├── index.html        Vite 入口
├── components.json    shadcn/ui 配置
├── tailwind.config.ts 设计令牌（品牌紫 + 语义色）
└── src/
    ├── App.tsx       四个 Tab 的壳
    ├── app-context.tsx 数据加载 / SSE / 全局弹窗与动作
    ├── components/ui/  shadcn 组件（button / dialog / select / table …）
    ├── components/dialogs/  新建任务 / 选文件夹 / 扫描 / 映射 / 报告 / 确认
    ├── pages/        任务列表 / 项目映射 / 数据源 / 系统设置
    └── lib/          API 契约、状态字典、Markdown 报告渲染
```

## 开发

```sh
uv run pytest
uv run ruff check src tests

# 前端（web/）
pnpm --dir web install
pnpm --dir web typecheck   # tsc --noEmit
pnpm --dir web build       # 产物 → src/taskagent/web/
pnpm --dir web dev         # 开发模式，需同时跑 taskagent serve
```

> `web/.npmrc` 里固定了 npmmirror 源（本机默认源证书失效时用）。若你的 npm 源正常，可直接删掉该文件。

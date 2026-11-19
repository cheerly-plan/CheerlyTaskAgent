# 父子需求识别与执行编排设计

> 目标：在现有「TAPD 需求 → 映射本地仓库 → CLI agent 执行 → 报告回填」链路上，支持 TAPD 的父子需求层级。
> 核心两件事：**怎么分清父子**（数据识别 + 建模）和**怎么完成**（执行编排 + 验收）。
>
> 已确认的决策：
> - 完成判定 = **全部子需求成功后，再拿父需求做一次验收判断**。
> - 上下文传递 = **把父需求标题 + 描述拼进子需求提示词**。
> - 本轮交付 = **详细设计文档**（本文），评审通过后再进入实现。
>
> 状态：**设计中，未实现**。父子树相关的字段、状态机、编排代码目前都不存在。

---

## 0. 现状与问题

### 0.1 现状事实（代码核对）

- 拉取的实体：`TapdConfig.entity_types` 默认 `["story", "bug"]`，`_ENTITIES` 还支持 `task`，但默认不拉。
- 归一化：`TapdClient._normalise()`（tapd.py:293）提取 `id / workspace_id / title / status / owner / priority / iteration_id / created / modified / git_url_hint`，**没有 parent 字段**；但完整对象保留在 `TapdItem.raw`。
- 建单：`RawItem.from_tapd()` → `parse_raw()` → `WorkItem.create()`，幂等键 `dedupe_key = source_ref = tapd:<type>:<id>`。每条需求/bug 建成一条**彼此独立**的 `WorkItem`。
- 数据模型：`WorkItem`（models.py:211）无 `parent_ref`、无子需求集合；`TapdItem` 同样没有。
- 执行：每条 `WorkItem` 独立调度，apply 在隔离 worktree 执行后同步回主仓库；并发度由调度器统一控制。
- 回填：完成后把状态写回 TAPD 并把报告路径写成评论。

结论：**系统目前是扁平的，父子关系信息只存在 TAPD 原始对象 `raw` 里，从未被利用。**

### 0.2 TAPD 侧父子关系的来源

| 关系 | 字段来源 | 说明 |
|---|---|---|
| 子需求 → 父需求 | `raw["parent_id"]`（story） | TAPD 父子需求，父 id 即 parent_id；顶层需求该字段为 `"0"` |
| 父 → 子 | `raw["children_id"]`（story） | 父需求侧的子列表，格式 `\|id1\|id2\|`（竖线分隔、首尾带 `\|`）；空值为 lone `"\|"` |
| 任务 → 所属需求 | `raw["story_id"]`（task） | task 归属某 story，可作为"父子"的一种（本次未拉 task，未实测） |

### 0.2.1 真实数据探查（2026-11-19，只读）

用已配置账号实时拉取两个项目（`32312011,35825536`）的 story/bug，结果：

- story 共 4 条，bug 0 条。原始对象里父子相关字段实测为 **`parent_id`** 和 **`children_id`**（无 `story_id`，因为默认不拉 task）。
- 找到一对真实父子：

| 需求 | id | parent_id | children_id |
|---|---|---|---|
| llm工具调用（父） | `1135825536001000036` | `0` | `\|1135825536001000058` |
| llm工具调用前端（子） | `1135825536001000058` | `1135825536001000036` | `\|` |

- 另两条（task_agent接入tapd、UI优化调整）均为顶层，`parent_id=0`、`children_id=|`。
- **这正暴露了要解决的问题**：父子通过 `parent_id`/`children_id` 双向互指，但本地已建的 3 条 TAPD 工单里两条父子需求都成了独立顶层任务，`parent_ref` 为空——关系在建单时丢失了。

> 结论：字段名 `parent_id` / `children_id` 已在真实环境验证；`children_id` 的竖线格式也确认。私有部署是否有差异仍需留意，字段名建议做成可配置常量（见 §7）。

### 0.3 要解决的核心矛盾

**worktree 同步冲突**：当前每个任务 apply 时基于主仓库 HEAD 建 worktree，完成后把改动同步回主仓库。若父需求下多个子需求指向**同一个本地仓库**且并行执行，后完成的会与先完成的在同步阶段冲突。因此执行编排必须按仓库分组：**同仓库子需求串行、不同仓库可并行**。这是整个设计的硬约束。

---

## 1. 目标模型

### 1.1 数据结构变更（向后兼容，全部可选字段）

`TapdItem`（tapd.py）新增：

```python
parent_id: str = ""        # 直接父 id（来自 raw parent_id / story_id）
parent_type: str = ""      # 父实体类型：story / bug / task
children_ids: list[str] = []  # 子 id 列表（来自 raw children_id，|id1|id2| 格式）
```

> `children_ids` 与 `parent_id` 双向冗余：识别时可优先信 `parent_id`，用 `children_ids` 交叉校验；父需求若因分页/状态过滤没拉全，`children_ids` 能提示"有子未拉取"。

`RawItem`（sources.py）新增：

```python
parent_ref: str = ""       # 稳定的父 source_ref，形如 tapd:story:<id>
```

`WorkItem`（models.py）新增：

```python
parent_ref: str = ""       # 父需求的 source_ref；顶层为空
is_container: bool = False # True = 父容器节点，不进执行队列
```

- 全部带默认值，历史数据 `from_dict()` 缺字段时自动取默认，无需数据迁移。
- `parent_ref` 指向父的 `source_ref`（`tapd:<type>:<id>`），与现有幂等键体系一致，不用另造 id。
- `to_dict()` / `summary()` / `from_dict()` 三处同步补字段（保持和 `git_url` 一样的处理模式）。

### 1.2 父容器语义

- 父需求正常建单（`source_ref` 不变），但 `is_container=True`：
  - 不进入执行队列，不被手动/自动 apply。
  - 作为子需求的上下文来源与完成判定锚点。
- 顶层需求（无 parent）保持现状，`is_container=False`、正常执行。
- 一个需求既是父（有子）又能被别人当成子（多级层级）：用 `parent_ref` 单层指针即可表达任意深度，UI 按需递归。

### 1.3 判定"谁是父"

导入阶段，对本次拉取的所有 item 建 `ref -> item` 索引：

```
对每个 item:
    pid = item.parent_id
    if pid 为空 or pid == "0":  → 顶层需求，is_container 视是否有子而定
    else:
        pref = f"tapd:{父类型}:{pid}"   # 父类型未知时默认 story，见 §7
        item.parent_ref = pref
        if pref 在本次集合:  → 标记该父为 container
        else:                → 孤儿子需求（见 §2.2）
```

---

## 2. 识别流程（怎么分清）

### 2.1 提取与建单

```text
TapdClient._normalise()
  └─ parent_id = first("parent_id", "story_id")   # story_id 仅对 task 有意义
     parent_type = "story" if 有 parent_id else ""

RawItem.from_tapd()
  └─ parent_ref = f"tapd:{parent_type}:{parent_id}" if parent_id else ""

parse_raw()
  └─ WorkItem.create(..., parent_ref=raw.parent_ref)
```

`dedupe_key` 仍是自身 `source_ref`，父子关系的建立不改变幂等语义——同一需求重复拉取只会更新，不会重复建单。

### 2.2 孤儿子需求补全

子需求有 `parent_ref`，但父不在本次拉取集合/不在本地 store 时：

1. 用 `TapdClient.fetch_one(parent_type, parent_id)` 拉父（tapd.py:239 已有此接口）。
2. 把父转成 `RawItem` 并建单，标记 `is_container=True`。
3. 拉不到（权限/已删除/不在项目）→ 子需求保留 `parent_ref`，但降级为**无父上下文**执行，并在任务上记一条提示（如 `parent_missing`），不阻断。
4. 补全要缓存，避免每次同步重复请求同一个父：内存里维护 `seen_parents` 集合。

> 这一步是本设计里唯一新增的 TAPD 读请求。默认每个孤儿一次 `fetch_one`，批量同步时数量可控。

### 2.3 父容器何时建、何时改

- 父本身被拉到：**照常建单**，随后由子需求存在这一事实把它标记为 container。
- 父没被拉到但被引用：按 §2.2 补建。
- 原本地已有父单但字段是旧的（无 `is_container`）：导入时若发现它有子，补写 `is_container=True`。

---

## 3. 上下文注入（父 → 子提示词）

按确认的决策：**执行子需求时，把父需求标题 + 描述拼进提示词开头**。

### 3.1 注入位置

在调度器构造提示词处（`scheduler.py` 的 prompt 构建逻辑，约 200 行附近），执行前按 `parent_ref` 查父 `WorkItem`：

```text
if work.parent_ref:
    parent = store.get_by_source_ref(work.parent_ref)   # 需新增按 source_ref 查询
    if parent:
        prefix = (
            f"你正在实现一个更大需求下的子任务。\n"
            f"父需求：{parent.title}\n"
            f"父需求描述：{parent.description}\n"
            f"---\n以下是本子任务：\n"
        )
        prompt = prefix + prompt
```

### 3.2 约束与取舍

- **长度**：父描述可能很长。设上限（如 2000 字），超出截断并标注「（父需求描述已截断）」，避免挤占子任务上下文。
- **缺失父**：查不到父时静默跳过注入，仅记日志，不阻断执行（与 §2.2 的降级一致）。
- **只注入直接父**：多级层级只取一层，避免提示词膨胀；更深层上下文由 TAPD 描述自包含解决。
- **报告留痕**：报告里记录本次注入了哪个父（`parent_ref` + 标题），便于回溯。
- 该改动只影响提示词文本，不改 CLI 调用协议、不改 worktree 逻辑。

---

## 4. 执行编排（怎么完成）

### 4.1 分组策略

调度一批子需求时，先按**映射到的本地仓库**分组：

```
groups: dict[repo_path, list[WorkItem]] = ...
```

- 无仓库映射的子需求：保持现有"待分配"状态，不进编排。
- 映射多个仓库的（TAPD 项目关联多个本地仓库）：当前本就待分配，同样不进编排，留给人工/后续 LLM 选仓库。
- 每组内子需求**串行**；组与组之间**并行**，复用调度器现有并发度上限。

### 4.2 串行保证与冲突规避

同仓库串行必须满足：**前一个子需求的 apply + 同步回主仓库完成后，后一个才从更新后的 HEAD 建 worktree**。

- 复用现有任务的终态判定（成功/失败/取消）。
- 前一个失败/取消时：后一个是继续还是跳过，见 §5 验收判定与 §7 待确认。本设计默认**继续跑完所有子需求**（失败的子需求单独记录，避免一个失败卡死整组）。

### 4.3 父需求不参与执行

- `is_container=True` 的任务在"可运行任务"筛选中排除。
- UI 上父需求展示为聚合节点（状态由子需求汇总，见 §5.2），不提供 apply 按钮。

---

## 5. 完成判定与验收（本次确认的核心）

用户确认：**全部子需求成功后，再拿父需求做一次验收判断**。

### 5.1 两级判定

```text
第一级（子需求完成）：
    children = 所有 parent_ref == 父.ref 且非 container 的 WorkItem
    all_done      = children 全部处于终态
    all_succeeded = children 全部 SUCCEEDED（含 apply 成功）

第二级（父需求验收）：
    if all_succeeded:
        触发一次「父需求验收」——以父任务的标题+描述作为验收标准，
        检查所有子需求的产出/报告是否满足父需求。
```

### 5.2 "拿父需求验收"的落地方式（待评审选定）

这是本次最需要拍板的技术点。三种实现路径，成本和确定性递减：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. 规则式汇总（本阶段推荐） | 不额外调 agent；生成一份"父需求验收报告"，汇编父描述 + 每个子需求的报告路径、状态、改动文件清单，供人核对 | 零新依赖、确定性、符合"本阶段不接 LLM"边界 | 只汇总不判断，需人看 |
| B. agent 验收任务 | 建一条特殊 `verify` 任务，把父描述 + 所有子报告喂给 CLI agent，让它判断"是否满足"，产出结论 | 自动判断 | 引入 agent 主观性；需新任务类型；结果需人工复核才可信 |
| C. 混合 | 先 A 生成汇总，再可选地跑 B，结论写入父任务报告 | 兼顾 | 工作量最大 |

> 现有架构明确"本阶段不接大模型/LLM 规划"，因此设计默认 **方案 A**：`all_succeeded` 后生成父需求验收汇总报告并回填父状态；是否启用 B 作为可选增强见 §7。

### 5.3 状态机

```
父需求状态：
    DISCOVERED ──有子且子在执行──▶ 进行中(汇总态)
    进行中 ──all_succeeded + 验收通过──▶ SUCCEEDED（回填 TAPD）
    进行中 ──任一子失败且终态──▶ 保持进行中/标记受阻（见 §7）
```

- 父状态是**汇总态**，不单独执行；由子需求状态推导。
- `all_done && all_succeeded` 是父收口的前置条件。
- 回填：父状态写回 TAPD（复用现有 `update_status`），验收汇总报告写成父需求的评论（复用现有 `add_comment`）。

### 5.4 汇总报告内容

```
# 父需求验收：<父标题>
- 父需求描述：<...>
- 子需求完成度：N/M 成功
- 子需求清单：
  | 子需求 | 状态 | 仓库 | 报告 | 改动文件 |
- 验收结论：（方案 A 下为"待人工核对"；方案 B 下为 agent 结论）
```

---

## 6. 涉及文件与改动清单

| 文件 | 改动 |
|---|---|
| `src/taskagent/tapd.py` | `TapdItem` 加 `parent_id/parent_type`；`_normalise` 提取；`parent_ref` 生成 |
| `src/taskagent/sources.py` | `RawItem` 加 `parent_ref`；`from_tapd` 透传 |
| `src/taskagent/models.py` | `WorkItem` 加 `parent_ref/is_container`；`to_dict/summary/from_dict/create` 同步 |
| `src/taskagent/scheduler.py` | 提示词注入父上下文；按仓库分组串行编排；父任务排除执行；验收判定 |
| `src/taskagent/api.py` | `preview/import` 透传 parent 字段；孤儿补全；父子树查询接口；按 `source_ref` 查任务 |
| `src/taskagent/store.py` | 新增 `get_by_source_ref`（若现有 `list` 不足） |
| `web/src/lib/api.ts` | `TapdPreviewItem/WorkItem` 类型补 parent 字段 |
| `web/src/pages/tasks-page.tsx`（及 task-card/table） | 树形/层级展示、父子状态汇总、父需求隐藏 apply |
| `tests/` | 字段提取、孤儿补全、分组串行、父完成判定、上下文注入的单元/集成测试 |

---

## 7. 待确认 / 风险

1. **父字段准确性**：~~需确认目标环境字段名~~ **已实测确认公网 `api.tapd.cn` 用 `parent_id` + `children_id`（见 §0.2.1）**；仅私有部署差异仍需留意，建议做成可配置常量。`children_id` 是 `|id1|id2|` 格式，解析时要按竖线 split 并去空。
2. **验收方案 A vs B**：默认 A（规则式汇总，不接 LLM）。是否要 B（agent 自动验收）需你定；B 与"本阶段不接 LLM"边界冲突。
3. **失败子需求的父状态**：任一子失败且终态时，父是保持"进行中"、标记"受阻"、还是按已完成度收口？默认保持进行中并标记受阻，等你确认。
4. **多级层级**：本设计用单层 `parent_ref`，任意深度可表达但只注入直接父上下文；是否需要完整的祖先链上下文。
5. **孤儿补全的请求量**：大批量同步时孤儿数量可能不少，`fetch_one` 次数需观测；可加批量接口或限流。
6. **同仓库串行的吞吐影响**：串行会降低同仓库多子的执行速度，但有助正确性；是否接受。
7. **`is_container` 与手动新建**：手动新建的任务是否允许作为父、如何指定父子（TAPD 自动识别 vs 手动选择）。

---

## 8. 落地顺序建议（评审通过后）

1. **数据层**：三个模型的字段 + 提取 + 序列化 + 单测。
2. **识别层**：`_normalise` / `from_tapd` / `parse_raw` / 孤儿补全 / 父容器标记 + 集成测试。
3. **上下文注入**：提示词前缀 + 截断 + 报告留痕 + 单测。
4. **编排层**：按仓库分组串行 + 父排除执行 + 并发复用 + 集成测试。
5. **完成判定**：两级判定 + 验收汇总报告 + TAPD 回填 + 测试。
6. **UI**：树形展示 + 父子状态汇总 + 隐藏父 apply。

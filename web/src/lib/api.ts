/** 后端 HTTP 契约的类型定义与极薄的 fetch 封装。 */

export type Kind = 'bug' | 'feature' | 'task';
export type Status =
  | 'discovered'
  | 'ready'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled';
export type ExecMode = 'dry-run' | 'apply';

export interface WorkItem {
  id: string;
  title: string;
  kind: Kind;
  source: string;
  source_ref?: string;
  git_url?: string;
  /** 父子需求：父的 source_ref；顶层为空。 */
  parent_ref?: string;
  /** 父需求标题快照：父不在本地时用它显示归属。 */
  parent_title?: string;
  /** 子需求的 source_ref 列表（仅父容器有值）。 */
  children_ids?: string[];
  /** True = 父容器节点，不执行。 */
  is_container?: boolean;
  repo?: string;
  status: Status;
  exec_mode: ExecMode;
  executor?: string;
  model?: string | null;
  attempt?: number;
  stage?: string | null;
  error?: string | null;
  commit?: string | null;
  report_path?: string | null;
  created_at?: string;
}

/** TAPD 同步预览中的一条候选（尚未落库）。 */
export interface TapdPreviewItem {
  source_ref: string;
  /** new=新增；changed=需求有更新；unchanged=本地已有且无变化。 */
  status: 'new' | 'changed' | 'unchanged';
  title: string;
  description: string;
  kind: Kind;
  external_url: string;
  modified: string;
  /** 根据 TAPD 项目绑定推断出的本地仓库名（空表示未推断到）。 */
  repo_name: string;
  /** 变更字段（status=changed 时非空）。 */
  changes: string[];
  existing_id: string;
  /** 父子需求：父的 source_ref；顶层为空。 */
  parent_ref?: string;
  /** 父需求标题快照：父不在本地时用它显示归属。 */
  parent_title?: string;
  /** True = 本次拉取里有子需求指向它，是父容器。 */
  is_container?: boolean;
}

export interface TapdPreviewResult {
  items: TapdPreviewItem[];
  cursor: string;
  nonce: string;
}

export interface TapdImportSelection {
  source_ref: string;
  executor?: string | null;
  model?: string | null;
  exec_mode?: ExecMode;
  repo_name?: string;
}

export interface ProjectMapping {
  id: string;
  name: string;
  path: string;
  enabled: boolean;
  note: string;
  tapd_workspace_ids: string[];  // 一个 TAPD 项目可关联多个本地仓库
  tapd_workspace_id: string;     // 兼容旧字段（第一个）
}

export interface TapdWorkspace {
  id: string;
  name: string;
  pretty_name: string;
  /** 所属企业 ID；下拉的企业选项从已缓存项目的这个字段提取。 */
  company_id: string;
}

export interface ModelChoice {
  id: string;
  label: string;
  /** 厂商标识（可选，来自本地模型缓存）。 */
  vendor?: string;
  /** 上下文窗口上限（max input tokens，可选）。 */
  context?: number;
  /** 去重时折叠掉的等价别名 id（如 hy4flash-free / hy4flash 同为 Hy4 flash）。 */
  aliases?: string[];
  /** codebuddy 最近一次会话实际使用的模型。 */
  current?: boolean;
  /**
   * 自定义模型：用户自己声明的（codebuddy 的 models.json / DSH 手写网关路由），
   * 而非 CLI 自带的内置模型。UI 据此在选项里标注「自定义」。
   */
  custom?: boolean;
}

export interface BackendInfo {
  id: string;
  label: string;
  command: string;
  enabled: boolean;
  /** 该后端可选的模型（结构化：id + 人类可读 label）。 */
  models?: ModelChoice[];
  /**
   * 该后端是否接受按任务指定模型。
   * false 表示模型钉死在自己的 profile 里且无法覆盖，此时 UI 只读展示默认模型。
   */
  model_selection?: boolean;
  /**
   * 任务未指定模型时该后端回落的模型 id（profile 里配置的默认）。
   * 空串表示无显式默认，落到 CLI 自己的默认（models 中 current 为 true 的那项）。
   */
  default_model?: string | null;
}

export interface AppConfig {
  version: string;
  executor: string;
  backends: BackendInfo[];
  models: ModelChoice[];
  default_model: string | null;
  max_concurrent?: number;
}

export interface TapdSettings {
  enabled: boolean;
  base_url: string;
  workspace_id: string;  // 多选项目 ID，逗号分隔
  company_id: string;
  projects: TapdWorkspace[];
  api_user: string;
  api_password: string;
  webhook_secret: string;
  entity_types: string[];
  pull_statuses: string[];
  status_on_success: string;
  status_on_failure: string;
  current_user: string;
  auto_pull: boolean;
  interval_minutes: number;
  exec_mode: ExecMode;
}

export interface BackendConfig {
  label: string;
  enabled: boolean;
  command: string;
  model: string;
  timeout_s: number;
  auto_sync: boolean;
}

/** 单一数据源对象（一个数据源、类型不同）。目前仅支持 type=tapd；其他类型留待后续接入。 */
export interface DataSource {
  type: string;
  name: string;
  enabled: boolean;
  /** type=tapd 时的专属配置。 */
  tapd: TapdSettings;
}

export interface UISettings {
  executor: string;
  codebuddy_command: string;
  dsh_path: string;
  default_model: string;
  max_concurrent: number;
  auto_sync: boolean;
  backends: Record<string, BackendConfig>;
  tapd: TapdSettings;
  data_source: DataSource;
}

export interface BrowseEntry {
  name: string;
  path: string;
  is_git: boolean;
}

export interface BrowseResult {
  current: string;
  parent: string | null;
  at_root: boolean;
  dirs: BrowseEntry[];
}

export interface ScanCandidate {
  name: string;
  path: string;
  is_git: boolean;
  candidate_name?: string;
  origin?: string | null;
}

export interface ScanResult {
  root: string;
  candidates: ScanCandidate[];
}

export interface MappingDraft {
  name: string;
  path: string;
  note: string;
  enabled?: boolean;
  tapd_workspace_ids?: string[];
  tapd_workspace_id?: string;
}

export type ItemAction = 'map' | 'dry' | 'apply' | 'exec' | 'cancel' | 'delete' | 'report';

export class ApiError extends Error {}

/** 报告接口返回 text/markdown 而非 JSON，单独走这条路径。 */
export async function fetchText(path: string): Promise<string> {
  const res = await fetch(path);
  if (!res.ok) throw new ApiError((await res.text()) || res.statusText);
  return res.text();
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let message = res.statusText;
    try {
      const payload = (await res.json()) as {detail?: string};
      if (payload?.detail) message = payload.detail;
    } catch {
      /* 后端可能返回空响应体 */
    }
    throw new ApiError(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(body),
});

export const endpoints = {
  config: () => api<AppConfig>('/api/config'),

  settings: () => api<UISettings>('/api/settings'),
  saveSettings: (body: UISettings) => api<UISettings>('/api/settings', jsonInit('PUT', body)),

  mappings: () => api<{mappings: ProjectMapping[]}>('/api/mappings'),
  createMapping: (body: MappingDraft) => api<ProjectMapping>('/api/mappings', jsonInit('POST', body)),
  updateMapping: (id: string, body: Partial<MappingDraft>) =>
    api<ProjectMapping>(`/api/mappings/${id}`, jsonInit('PUT', body)),
  deleteMapping: (id: string) => api<{deleted: boolean}>(`/api/mappings/${id}`, {method: 'DELETE'}),
  createMappingsBulk: (items: MappingDraft[]) =>
    api<{added: ProjectMapping[]; failed: {item: MappingDraft; error: string}[]}>(
      '/api/mappings/bulk',
      jsonInit('POST', {items}),
    ),

  items: (params?: {limit?: number; offset?: number}) =>
    api<{items: WorkItem[]; total?: number}>(`/api/items?limit=${params?.limit ?? 1000}&offset=${params?.offset ?? 0}`),
  mapItem: (id: string, repo: string) =>
    api<WorkItem>(`/api/items/${id}/map`, jsonInit('POST', {repo_name: repo})),
  runItem: (id: string, body: {exec_mode: ExecMode; model?: string | null; approved?: boolean; executor?: string}) =>
    api<unknown>(`/api/items/${id}/run`, jsonInit('POST', body)),
  cancelItem: (id: string) => api<{cancelled: boolean}>(`/api/items/${id}/cancel`, {method: 'POST'}),
  autoMapItems: () =>
    api<{mapped: number; ambiguous: number; unmatched: number}>('/api/items/auto-map', {method: 'POST'}),
  deleteItem: (id: string) => api<unknown>(`/api/items/${id}`, {method: 'DELETE'}),
  report: (id: string) => fetchText(`/api/items/${encodeURIComponent(id)}/report`),
  reportUrl: (id: string) => `/api/items/${encodeURIComponent(id)}/report`,

  createManualTask: (body: {
    title: string;
    description: string;
    kind: Kind;
    repo_name: string;
    exec_mode: ExecMode;
    executor: string;
    model: string | null;
    run_now: boolean;
    approved: boolean;
  }) => api<WorkItem>('/api/tasks/manual', jsonInit('POST', body)),

  syncTapd: (body?: {modified_since?: string; exec_mode?: string; force?: boolean}) =>
    api<{created: number; skipped: number}>('/api/tapd/sync', jsonInit('POST', body ?? {})),

  /** 预览 TAPD 变更，不落库。 */
  tapdPreview: (body?: {modified_since?: string; force?: boolean}) =>
    api<TapdPreviewResult>('/api/tapd/preview', jsonInit('POST', body ?? {})),

  /** 确认导入勾选的预览条目。 */
  tapdImport: (body: {items: TapdImportSelection[]; cursor: string}) =>
    api<{created: number; updated: number; skipped: number}>(
      '/api/tapd/import', jsonInit('POST', body),
    ),

  tapdWorkspaces: (body?: {company_id?: string}) =>
    api<{projects: TapdWorkspace[]}>('/api/tapd/workspaces', jsonInit('POST', body ?? {})),

  tapdWebhookUrl: () => `${location.origin}/api/tapd/webhook`,

  probeBackend: (id: string) =>
    api<{id: string; model_selection: boolean; models: ModelChoice[]; error?: string}>(
      `/api/backends/${encodeURIComponent(id)}/probe`,
      {method: 'POST'},
    ),

  browse: (path: string) => api<BrowseResult>(`/api/fs/browse?path=${encodeURIComponent(path)}`),
  scan: (path: string, depth = 2) =>
    api<ScanResult>('/api/fs/scan', jsonInit('POST', {path, depth})),
};

/**
 * 读取某个文件夹自身的 git origin（复用 depth=0 的单目录扫描）。
 * 非 git 目录、路径为空或请求失败时返回空串，不抛错、不阻塞调用方。
 */
export async function readGitOrigin(path: string): Promise<string> {
  if (!path) return '';
  try {
    const result = await endpoints.scan(path, 0);
    const self = result.candidates?.find((c) => c.path === path) ?? result.candidates?.[0];
    return self?.origin?.trim() || '';
  } catch {
    return '';
  }
}
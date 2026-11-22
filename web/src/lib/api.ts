
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
  parent_ref?: string;
  parent_title?: string;
  children_ids?: string[];
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

export interface RouteDecision {
  repo: string;
  method: string;
  confidence: number;
  reason: string;
  candidates?: {repo: string; score: number; reason: string}[];
  needs_confirm?: boolean;
}

export interface RepoProfile {
  mapping_id: string;
  name: string;
  path: string;
  roles: string[];
  package_name: string;
  description: string;
  keywords: string[];
  readme_title: string;
  readme_excerpt: string;
  top_dirs: string[];
  languages: Record<string, number>;
  fingerprints: string[];
  commit_keywords: string[];
  head_sha: string;
  built_at: string;
  override_roles: string[];
  override_keywords: string[];
}

export interface TapdPreviewItem {
  source_ref: string;
  status: 'new' | 'changed' | 'unchanged';
  title: string;
  description: string;
  kind: Kind;
  external_url: string;
  modified: string;
  repo_name: string;
  route?: RouteDecision;
  changes: string[];
  existing_id: string;
  parent_ref?: string;
  parent_title?: string;
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
  tapd_workspace_ids: string[];
  tapd_workspace_id: string;
}

export interface TapdWorkspace {
  id: string;
  name: string;
  pretty_name: string;
  company_id: string;
}

export interface ModelChoice {
  id: string;
  label: string;
  vendor?: string;
  context?: number;
  aliases?: string[];
  current?: boolean;
  custom?: boolean;
}

export interface BackendInfo {
  id: string;
  label: string;
  command: string;
  enabled: boolean;
  models?: ModelChoice[];
  model_selection?: boolean;
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
  workspace_id: string;
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

export interface DataSource {
  type: string;
  name: string;
  enabled: boolean;
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

export async function fetchText(path: string): Promise<string> {
  const res = await fetch(path);
  if (!res.ok) throw new ApiError((await res.text()) || res.statusText);
  return res.text();
}

const inflightGets = new Map<string, Promise<unknown>>();

async function doApi<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let message = res.statusText;
    try {
      const payload = (await res.json()) as {detail?: string};
      if (payload?.detail) message = payload.detail;
    } catch {
    }
    throw new ApiError(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase();
  const cacheable = method === 'GET' && !init?.signal;
  if (!cacheable) return doApi<T>(path, init);

  const key = path;
  const existing = inflightGets.get(key);
  if (existing) return existing as Promise<T>;

  const pending = doApi<T>(path, init);
  inflightGets.set(key, pending);
  void pending.finally(() => {
    if (inflightGets.get(key) === pending) inflightGets.delete(key);
  });
  return pending;
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
  repoProfiles: () => api<{profiles: RepoProfile[]}>('/api/repo-profiles'),
  rebuildRepoProfiles: () =>
    api<{created: number; rebuilt: number; pruned: number; profiles: RepoProfile[]}>(
      '/api/repo-profiles/rebuild',
      {method: 'POST'},
    ),
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

  tapdPreview: (body?: {modified_since?: string; force?: boolean}) =>
    api<TapdPreviewResult>('/api/tapd/preview', jsonInit('POST', body ?? {})),

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
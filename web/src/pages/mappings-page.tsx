import * as React from 'react';
import {FolderGit2, GitBranch, Pencil, RefreshCw, Search, Sparkles, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {EmptyState, Mono} from '@/components/status-bits';
import {VerifyNaDialog} from '@/components/dialogs/verify-na-dialog';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Switch} from '@/components/ui/switch';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {Tooltip, TooltipContent, TooltipTrigger} from '@/components/ui/tooltip';
import {endpoints, readGitOrigin, type RepoProfile, type TapdWorkspace, type VerifyRepo} from '@/lib/api';
import {cn} from '@/lib/utils';
import {toast} from 'sonner';

type DeliveryBadge = {
  label: string;
  variant: 'outline' | 'muted' | 'info' | 'purple' | 'success';
  title: string;
};

function deliveryBadge(mapping: {delivery?: string; merge_policy?: string}): DeliveryBadge {
  const mode = mapping.delivery ?? '';
  const merge = mapping.merge_policy === 'auto'
    ? '自动合并'
    : mapping.merge_policy === 'manual' ? '人工合并' : '跟随系统设置';
  switch (mode) {
    case '':
      return {label: '跟随', variant: 'outline', title: '交付与合并策略跟随「系统设置 → 交付」'};
    case 'off':
      return {label: '关闭', variant: 'muted', title: '只写主仓库工作区，不提交 / 不推送'};
    case 'commit':
      return {label: '提交', variant: 'info', title: '任务分支上做过滤提交，不推送'};
    case 'push':
      return {label: '推送', variant: 'info', title: '过滤提交后推送任务分支，不创建 PR'};
    case 'pr':
      return {
        label: merge === '自动合并' ? 'PR·自动' : merge === '人工合并' ? 'PR·人工' : 'PR',
        variant: merge === '自动合并' ? 'purple' : 'success',
        title: `创建 PR/MR；合并策略：${merge}`,
      };
    default:
      return {label: mode, variant: 'outline', title: mode};
  }
}

function shortPath(path: string): string {
  const parts = path.split('/').filter(Boolean);
  return parts.length <= 3 ? path : `…/${parts.slice(-2).join('/')}`;
}

function isGitNote(note: string): boolean {
  return /^\s*git:\s*\S+/i.test(note);
}

function shortNote(note: string): string {
  const git = note.match(/^\s*git:\s*(\S+)\s*$/i);
  if (!git) return note;
  const parts = git[1]
    .replace(/\.git$/, '')
    .split(/[/:]/)
    .filter(Boolean);
  const repo = parts[parts.length - 1] ?? '';
  return repo || note;
}

function IconTip({label, children}: {label: string; children: React.ReactNode}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" className="bg-[#1f1f28] px-2.5 py-1 text-[11px] text-white">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function MappingsPage() {
  const {settings, mappings, pickFolder, openScan, openMappingModal, toggleMapping, deleteMapping, confirm} = useApp();
  const [fetchedProjects, setFetchedProjects] = React.useState<TapdWorkspace[]>([]);
  const [profiles, setProfiles] = React.useState<Record<string, RepoProfile>>({});
  const [rebuilding, setRebuilding] = React.useState(false);
  const [verifyRepos, setVerifyRepos] = React.useState<Record<string, VerifyRepo>>({});
  const [naDialog, setNaDialog] = React.useState<{open: boolean; repo: VerifyRepo | null}>({
    open: false, repo: null,
  });

  const loadProfiles = React.useCallback(async () => {
    try {
      const result = await endpoints.repoProfiles();
      const map: Record<string, RepoProfile> = {};
      for (const profile of result.profiles ?? []) map[profile.mapping_id] = profile;
      setProfiles(map);
    } catch {
    }
  }, []);

  React.useEffect(() => {
    void loadProfiles();
  }, [loadProfiles, mappings.length]);

  const loadVerify = React.useCallback(async () => {
    try {
      const result = await endpoints.verifyConfig();
      const map: Record<string, VerifyRepo> = {};
      for (const repo of result.repos ?? []) map[repo.mapping_id] = repo;
      setVerifyRepos(map);
    } catch {
    }
  }, []);

  React.useEffect(() => {
    void loadVerify();
  }, [loadVerify, mappings.length]);

  const rebuildAll = async () => {
    const ok = await confirm(
      '将按仓库文件重建「仓库画像」与「验证配置」；验证命令的手工修改会被覆盖。继续？',
    );
    if (!ok) return;
    setRebuilding(true);
    const [profileResult, verifyResult] = await Promise.allSettled([
      endpoints.rebuildRepoProfiles(),
      endpoints.rebuildVerifyConfig(),
    ]);
    let verifyCount = 0;
    if (profileResult.status === 'fulfilled') {
      const map: Record<string, RepoProfile> = {};
      for (const profile of profileResult.value.profiles ?? []) map[profile.mapping_id] = profile;
      setProfiles(map);
    }
    if (verifyResult.status === 'fulfilled') {
      const map: Record<string, VerifyRepo> = {};
      for (const repo of verifyResult.value.repos ?? []) map[repo.mapping_id] = repo;
      setVerifyRepos(map);
      verifyCount = (verifyResult.value.created ?? 0) + (verifyResult.value.rebuilt ?? 0);
    }
    setRebuilding(false);
    if (profileResult.status === 'fulfilled' && verifyResult.status === 'fulfilled') {
      toast.success(`已重建画像与验证配置（验证 ${verifyCount} 个仓库）`);
    } else {
      toast.error('部分重建失败，请重试');
    }
  };

  React.useEffect(() => {
    const cid = (settings?.tapd?.company_id || '').trim();
    if (!cid || !settings?.tapd?.api_user || fetchedProjects.length) return;
    void endpoints.tapdWorkspaces({company_id: cid})
      .then((result) => setFetchedProjects(result.projects ?? []))
      .catch(() => undefined);
  }, [settings?.tapd?.company_id, settings?.tapd?.api_user, fetchedProjects.length]);

  const tapdProjects = [...(settings?.tapd?.projects ?? []), ...fetchedProjects].filter(
    (project, index, list) => list.findIndex((item) => item.id === project.id) === index,
  );
  const projectLabel = (id: string) =>
    tapdProjects.find((project) => project.id === id)?.name || id;

  const pickThenMap = () =>
    pickFolder((path) => {
      const name = path.split('/').filter(Boolean).pop() ?? path;
      void readGitOrigin(path).then((origin) => {
        openMappingModal({
          id: '',
          name,
          path,
          note: origin ? `git: ${origin}` : '',
          enabled: true,
          tapd_workspace_ids: [],
          tapd_workspace_id: '',
        });
      });
    });

  return (
    <>
    <Card className="flex max-h-full min-h-0 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <h3 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">项目映射</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void rebuildAll()}
            disabled={rebuilding}
            title="按仓库文件重建仓库画像与验证配置"
          >
            <RefreshCw className={rebuilding ? 'animate-spin' : ''} />
            重建数据
          </Button>
          <Button variant="outline" size="sm" onClick={() => pickFolder((path) => openScan(path))}>
            <Search />
            扫描
          </Button>
          <Button  size="sm" onClick={pickThenMap}>
            <FolderGit2 />
            添加
          </Button>
        </div>
      </div>

      {mappings.length === 0 ? (
        <EmptyState
          icon={FolderGit2}
          title="暂无项目映射"
        />
      ) : (
        <Table className="min-w-[1120px] table-fixed" containerClassName="min-h-0 min-w-0 flex-1">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[160px] pl-5">项目名</TableHead>
              <TableHead className="w-[180px]">本地路径</TableHead>
              <TableHead className="w-[150px]">仓库信息 / 备注</TableHead>
              <TableHead className="w-[120px]">仓库画像</TableHead>
              <TableHead className="w-[130px]">验证配置</TableHead>
              <TableHead className="w-[140px]">TAPD 项目</TableHead>
              <TableHead className="w-[90px]">交付</TableHead>
              <TableHead className="w-[104px]">状态</TableHead>
              <TableHead className="sticky right-0 z-20 w-[88px] bg-[#fafafd] pr-5 text-right shadow-[-8px_0_8px_-8px_rgba(15,17,21,0.18)]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mappings.map((mapping) => (
              <TableRow key={mapping.id} className="[&>td]:py-2.5">
                <TableCell className="pl-5">
                  <span className="block truncate font-semibold text-foreground" title={mapping.name}>
                    {mapping.name}
                  </span>
                </TableCell>
                <TableCell>
                  <Mono
                    className="block max-w-full truncate bg-transparent px-0 py-0 text-[12px] text-foreground/55"
                    title={mapping.path}
                  >
                    {shortPath(mapping.path)}
                  </Mono>
                </TableCell>
                <TableCell>
                  {mapping.note ? (
                    <span
                      className="flex min-w-0 items-center gap-1 text-[12px] text-foreground/55"
                      title={mapping.note}
                    >
                      {isGitNote(mapping.note) ? (
                        <GitBranch className="size-3 shrink-0 text-faint" />
                      ) : null}
                      <span className="truncate">{shortNote(mapping.note)}</span>
                    </span>
                  ) : (
                    <span className="text-xs text-faint">—</span>
                  )}
                </TableCell>
                <TableCell>
                  {(() => {
                    const profile = profiles[mapping.id];
                    const roles = profile?.override_roles?.length
                      ? profile.override_roles
                      : profile?.roles ?? [];
                    if (!roles.length) {
                      return <span className="text-xs text-faint">—</span>;
                    }
                    const label: Record<string, string> = {
                      frontend: '前端',
                      backend: '后端',
                      mobile: '移动端',
                      desktop: '桌面端',
                    };
                    const text = roles.map((role) => label[role] ?? role).join(' · ');
                    return (
                      <span
                        className="flex items-center gap-1 whitespace-nowrap text-[12px] text-[#6d3fc0]"
                        title={text}
                      >
                        <Sparkles className="size-3 shrink-0" />
                        <span className="truncate">{text}</span>
                      </span>
                    );
                  })()}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  {(() => {
                    const verify = verifyRepos[mapping.id];
                    if (!verify) return <span className="text-xs text-faint">—</span>;
                    if (!verify.checks?.length) {
                      return <span className="text-xs text-faint">未探测到</span>;
                    }
                    const missing = verify.missing_categories ?? [];
                    const naCount = Object.keys(verify.na ?? {}).length;
                    const naTitle = Object.entries(verify.na ?? {})
                      .map(([category, reason]) => `${category}: ${reason}`)
                      .join('\n');
                    return (
                      <div className="flex items-center gap-1.5">
                        {missing.length ? (
                          <Badge
                            variant="destructive-muted"
                            title={`缺少验证类别：${missing.join('、')}`}
                          >
                            缺 {missing.length} 类
                          </Badge>
                        ) : (
                          <Badge
                            variant={verify.stale ? 'warning' : 'success'}
                            title={verify.stale ? '仓库文件已变化，建议重建验证配置' : '四类检查齐全'}
                          >
                            {verify.checks.length} 项
                          </Badge>
                        )}
                        {naCount ? (
                          <span className="text-[11px] text-faint" title={naTitle || undefined}>
                            N/A {naCount}
                          </span>
                        ) : null}
                        <button
                          type="button"
                          className="rounded p-0.5 text-faint transition-colors hover:bg-secondary hover:text-primary"
                          title="编辑 N/A（理由与过期时间）"
                          aria-label={`编辑 ${mapping.name} 的 N/A`}
                          onClick={() => setNaDialog({open: true, repo: verify})}
                        >
                          <Pencil className="size-3" />
                        </button>
                      </div>
                    );
                  })()}
                </TableCell>
                <TableCell>
                  {(() => {
                    const ids = mapping.tapd_workspace_ids?.length
                      ? mapping.tapd_workspace_ids
                      : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
                    if (!ids.length) return <span className="text-xs text-faint">—</span>;
                    const shown = ids.slice(0, 2);
                    const rest = ids.length - shown.length;
                    return (
                      <div className="flex flex-wrap items-center gap-1">
                        {shown.map((id) => (
                          <Badge
                            key={id}
                            variant="info"
                            className="max-w-[106px] truncate"
                            title={projectLabel(id)}
                          >
                            {projectLabel(id)}
                          </Badge>
                        ))}
                        {rest > 0 ? (
                          <span title={ids.slice(2).map(projectLabel).join('、')}>
                            <Badge variant="muted">+{rest}</Badge>
                          </span>
                        ) : null}
                      </div>
                    );
                  })()}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  {(() => {
                    const badge = deliveryBadge(mapping);
                    return (
                      <Badge
                        variant={badge.variant}
                        className="whitespace-nowrap"
                        title={badge.title}
                      >
                        {badge.label}
                      </Badge>
                    );
                  })()}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={mapping.enabled}
                      onCheckedChange={() => void toggleMapping(mapping.id)}
                      aria-label={`${mapping.enabled ? '停用' : '启用'} ${mapping.name}`}
                    />
                    <span className={cn('whitespace-nowrap text-[11.5px]', mapping.enabled ? 'text-foreground/70' : 'text-faint')}>
                      {mapping.enabled ? '启用' : '停用'}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="sticky right-0 z-10 bg-card pr-5 shadow-[-8px_0_8px_-8px_rgba(15,17,21,0.12)] group-hover:bg-[#f8f7fd]">
                  <div className="flex items-center justify-end gap-1">
                    <IconTip label="编辑">
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        onClick={() => openMappingModal(mapping)}
                        aria-label={`编辑 ${mapping.name}`}
                      >
                        <Pencil />
                      </Button>
                    </IconTip>
                    <IconTip label="删除">
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        className="text-foreground/45 hover:text-destructive"
                        onClick={() => void deleteMapping(mapping.id)}
                        aria-label={`删除 ${mapping.name}`}
                      >
                        <Trash2 />
                      </Button>
                    </IconTip>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
    <VerifyNaDialog
      open={naDialog.open}
      repo={naDialog.repo}
      onOpenChange={(open) => setNaDialog({open, repo: open ? naDialog.repo : null})}
      onSaved={(repos) => {
        const map: Record<string, VerifyRepo> = {};
        for (const repo of repos ?? []) map[repo.mapping_id] = repo;
        setVerifyRepos(map);
        setNaDialog({open: false, repo: null});
      }}
    />
    </>
  );
}
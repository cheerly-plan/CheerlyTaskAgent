import * as React from 'react';
import {FolderGit2, Pencil, Plus, RefreshCw, Search, Sparkles, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {EmptyState, Mono} from '@/components/status-bits';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Switch} from '@/components/ui/switch';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {endpoints, readGitOrigin, type RepoProfile, type TapdWorkspace} from '@/lib/api';
import {cn} from '@/lib/utils';

export function MappingsPage() {
  const {settings, mappings, pickFolder, openScan, openMappingModal, toggleMapping, deleteMapping} = useApp();
  const [fetchedProjects, setFetchedProjects] = React.useState<TapdWorkspace[]>([]);
  const [profiles, setProfiles] = React.useState<Record<string, RepoProfile>>({});
  const [rebuilding, setRebuilding] = React.useState(false);

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

  const rebuildProfiles = async () => {
    setRebuilding(true);
    try {
      const result = await endpoints.rebuildRepoProfiles();
      const map: Record<string, RepoProfile> = {};
      for (const profile of result.profiles ?? []) map[profile.mapping_id] = profile;
      setProfiles(map);
    } finally {
      setRebuilding(false);
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
    <Card className="flex max-h-full min-h-0 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <h3 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground">项目映射</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={pickThenMap}>
            <FolderGit2 />
            添加
          </Button>
          <Button variant="outline" size="sm" onClick={() => pickFolder((path) => openScan(path))}>
            <Search />
            扫描
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void rebuildProfiles()}
          >
            <RefreshCw className={rebuilding ? 'animate-spin' : ''} />
            重建画像
          </Button>
          <Button size="sm" onClick={() => openMappingModal(null)}>
            <Plus />
            新增
          </Button>
        </div>
      </div>

      {mappings.length === 0 ? (
        <EmptyState
          icon={FolderGit2}
          title="暂无项目映射"
        />
      ) : (
        <Table className="min-w-[980px]" containerClassName="min-h-0 flex-1">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-5">项目名</TableHead>
              <TableHead>本地路径</TableHead>
              <TableHead>仓库信息 / 备注</TableHead>
              <TableHead>仓库画像</TableHead>
              <TableHead>TAPD 项目</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="pr-5 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mappings.map((mapping) => (
              <TableRow key={mapping.id}>
                <TableCell className="pl-5 font-semibold text-foreground">{mapping.name}</TableCell>
                <TableCell>
                  <Mono className="block max-w-[200px] truncate">
                    {mapping.path}
                  </Mono>
                </TableCell>
                <TableCell>
                  <span
                    className="block max-w-[220px] truncate text-[12.5px] text-foreground/70"
                  >
                    {mapping.note || '—'}
                  </span>
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
                    return (
                      <div className="flex flex-wrap gap-1">
                        {roles.map((role) => (
                          <Badge
                            key={role}
                            variant="purple"
                            className="gap-1"
                          >
                            <Sparkles className="size-2.5" />
                            {label[role] ?? role}
                          </Badge>
                        ))}
                      </div>
                    );
                  })()}
                </TableCell>
                <TableCell>
                  {(() => {
                    const ids = mapping.tapd_workspace_ids?.length
                      ? mapping.tapd_workspace_ids
                      : mapping.tapd_workspace_id ? [mapping.tapd_workspace_id] : [];
                    return ids.length ? (
                      <div className="flex flex-wrap gap-1">
                        {ids.map((id) => (
                          <Badge key={id} variant="info">
                            {projectLabel(id)}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-faint">—</span>
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
                    <span className={cn('whitespace-nowrap text-xs', mapping.enabled ? 'text-foreground/80' : 'text-faint')}>
                      {mapping.enabled ? '启用' : '停用'}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="pr-5">
                  <div className="flex items-center justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openMappingModal(mapping)}>
                      <Pencil />
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => void deleteMapping(mapping.id)}
                      aria-label={`删除 ${mapping.name}`}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}
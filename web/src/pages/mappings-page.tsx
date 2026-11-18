import {FolderGit2, Pencil, Plus, Search, Trash2} from 'lucide-react';

import {useApp} from '@/app-context';
import {EmptyState, Mono} from '@/components/status-bits';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card} from '@/components/ui/card';
import {Switch} from '@/components/ui/switch';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table';
import {cn} from '@/lib/utils';

export function MappingsPage() {
  const {mappings, pickFolder, openScan, openMappingModal, toggleMapping, deleteMapping} = useApp();

  const pickThenMap = () =>
    pickFolder((path) => {
      const name = path.split('/').filter(Boolean).pop() ?? path;
      openMappingModal({
        id: '',
        name,
        aliases: [name],
        path,
        note: '',
        enabled: true,
      });
    });

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
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
          description="点右上角「手动新增」直接填写，或用「选择 / 扫描文件夹」从本地目录批量导入。"
        />
      ) : (
        <Table className="min-w-[860px]" containerClassName="max-h-[56vh]">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-5">项目名</TableHead>
              <TableHead>别名</TableHead>
              <TableHead>本地路径</TableHead>
              <TableHead>仓库信息 / 备注</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="pr-5 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mappings.map((mapping) => (
              <TableRow key={mapping.id}>
                <TableCell className="pl-5 font-semibold text-foreground">{mapping.name}</TableCell>
                <TableCell>
                  {mapping.aliases?.length ? (
                    <div className="flex flex-wrap gap-1">
                      {mapping.aliases.map((alias) => (
                        <Badge key={alias} variant="info">
                          {alias}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-xs text-faint">用项目名匹配</span>
                  )}
                </TableCell>
                <TableCell>
                  <Mono className="break-all">{mapping.path}</Mono>
                </TableCell>
                <TableCell>
                  <span
                    className="line-clamp-2 block max-w-[320px] text-[12.5px] text-foreground/70"
                    title={mapping.note}
                  >
                    {mapping.note || '—'}
                  </span>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={mapping.enabled}
                      onCheckedChange={() => void toggleMapping(mapping.id)}
                      aria-label={`${mapping.enabled ? '停用' : '启用'} ${mapping.name}`}
                    />
                    <span className={cn('text-xs', mapping.enabled ? 'text-foreground/80' : 'text-faint')}>
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

      <div className="border-t border-border bg-[#fbfbfd] px-5 py-2.5 text-[11.5px] text-faint">
        目录浏览与扫描为只读且限制在主目录内；不会改动文件系统。
      </div>
    </Card>
  );
}
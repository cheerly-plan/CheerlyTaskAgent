import * as React from 'react';
import {FolderGit2, Inbox, List, Settings2} from 'lucide-react';

import {AppProvider, useApp} from '@/app-context';
import {AppHeader} from '@/components/app-header';
import {DataSourcesPage} from '@/pages/data-sources-page';
import {MappingsPage} from '@/pages/mappings-page';
import {SettingsPage} from '@/pages/settings-page';
import {TasksPage} from '@/pages/tasks-page';
import {Toaster} from '@/components/ui/sonner';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '@/components/ui/tabs';

type PageKey = 'tasks' | 'mapping' | 'sources' | 'settings';

const TABS = [
  {key: 'tasks' as const, label: '任务列表', icon: List},
  {key: 'mapping' as const, label: '项目映射', icon: FolderGit2},
  {key: 'sources' as const, label: '数据源', icon: Inbox},
  {key: 'settings' as const, label: '系统设置', icon: Settings2},
];

function Shell() {
  const {conn, connText, openNewTask} = useApp();
  const [page, setPage] = React.useState<PageKey>('tasks');

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <AppHeader conn={conn} connText={connText} onNewTask={openNewTask} />

      <main className="mx-auto flex w-full max-w-[1520px] min-h-0 flex-1 flex-col px-5 pb-4 pt-6 lg:px-8">
        <Tabs
          value={page}
          onValueChange={(value) => setPage(value as PageKey)}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList className="h-auto w-full shrink-0 justify-start gap-6 rounded-none border-b border-border bg-transparent p-0">
            {TABS.map((tab) => (
              <TabsTrigger
                key={tab.key}
                value={tab.key}
                className="group gap-1.5 rounded-none border-b-[2.5px] border-transparent px-0.5 pb-3 pt-2 text-[13.5px] font-medium text-foreground/60 shadow-none transition-colors hover:text-foreground focus-visible:bg-primary-muted/60 focus-visible:ring-0 data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:font-semibold data-[state=active]:text-primary data-[state=active]:shadow-none"
              >
                <tab.icon className="size-4 opacity-80" />
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="tasks" className="mt-6 min-h-0 flex-1 overflow-y-auto">
            <TasksPage />
          </TabsContent>
          <TabsContent value="mapping" className="mt-6 min-h-0 flex-1 overflow-y-auto">
            <MappingsPage />
          </TabsContent>
          <TabsContent value="sources" className="mt-6 min-h-0 flex-1 overflow-y-auto -mx-[calc(50vw-50%)] px-5 pb-4 lg:px-8">
            <div className="mx-auto w-full max-w-[1520px]">
              <DataSourcesPage />
            </div>
          </TabsContent>
          <TabsContent value="settings" className="mt-6 min-h-0 flex-1 overflow-y-auto">
            <SettingsPage />
          </TabsContent>
        </Tabs>
      </main>

      <Toaster position="top-right" richColors closeButton />
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
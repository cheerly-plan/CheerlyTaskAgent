import * as React from 'react';

import {AppProvider, useApp} from '@/app-context';
import {AppSidebar, MobileBar, type PageKey} from '@/components/app-sidebar';
import {DashboardPage} from '@/pages/dashboard-page';
import {DataSourcesPage} from '@/pages/data-sources-page';
import {MappingsPage} from '@/pages/mappings-page';
import {SettingsPage} from '@/pages/settings-page';
import {TasksPage} from '@/pages/tasks-page';
import {Toaster} from '@/components/ui/sonner';

function Shell() {
  const {conn, connText, openNewTask} = useApp();
  const [page, setPage] = React.useState<PageKey>('dashboard');

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <AppSidebar page={page} onPage={setPage} conn={conn} connText={connText} />

      <div className="flex min-w-0 flex-1 flex-col">
        <MobileBar page={page} onPage={setPage} onNewTask={openNewTask} />
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-5 lg:px-6">
          {page === 'dashboard' ? <DashboardPage onOpenTasks={() => setPage('tasks')} /> : null}
          {page === 'tasks' ? <TasksPage /> : null}
          {page === 'mapping' ? <MappingsPage /> : null}
          {page === 'sources' ? <DataSourcesPage /> : null}
          {page === 'settings' ? <SettingsPage /> : null}
        </main>
      </div>

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

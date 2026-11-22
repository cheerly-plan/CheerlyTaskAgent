import * as React from 'react';
import {Plus} from 'lucide-react';

import {AppProvider, useApp} from '@/app-context';
import {AppSidebar, ConnBadge, NAV_ITEMS, type PageKey} from '@/components/app-sidebar';
import {BrandMark} from '@/components/brand-mark';
import {Button} from '@/components/ui/button';
import {SelectField} from '@/components/select-field';
import {DashboardPage} from '@/pages/dashboard-page';
import {DataSourcesPage} from '@/pages/data-sources-page';
import {HistoryPage} from '@/pages/history-page';
import {ReportPage} from '@/pages/report-page';
import {MappingsPage} from '@/pages/mappings-page';
import {SettingsPage} from '@/pages/settings-page';
import {TasksPage} from '@/pages/tasks-page';
import {Toaster} from '@/components/ui/sonner';
import {TooltipProvider} from '@/components/ui/tooltip';

function Shell() {
  const {conn, connText, openNewTask, page, setPage, reportView, closeReport} = useApp();

  const goPage = (next: PageKey) => {
    if (next !== 'report') closeReport();
    setPage(next);
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4 lg:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark size={26} />
          <span className="truncate text-[14.5px] font-semibold tracking-[-0.02em] text-foreground">
            CheerlyTaskAgent
          </span>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <ConnBadge conn={conn} connText={connText} />
          <div className="flex items-center gap-2 lg:hidden">
            <SelectField
              size="sm"
              className="w-[116px]"
              value={page}
              options={NAV_ITEMS.map((item) => ({value: item.key, label: item.label}))}
              onChange={(value) => goPage(value as PageKey)}
            />
            <Button size="icon-sm" onClick={openNewTask} aria-label="新建任务">
              <Plus />
            </Button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <AppSidebar page={page} onPage={goPage} reportFrom={reportView.backFrom} />
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-5 lg:px-6">
          {page === 'dashboard' ? <DashboardPage /> : null}
          {page === 'tasks' ? <TasksPage /> : null}
          {page === 'history' ? <HistoryPage /> : null}
          {page === 'mapping' ? <MappingsPage /> : null}
          {page === 'sources' ? <DataSourcesPage /> : null}
          {page === 'settings' ? <SettingsPage /> : null}
          {page === 'report' ? <ReportPage /> : null}
        </main>
      </div>

      <Toaster position="top-right" richColors closeButton />
    </div>
  );
}

export default function App() {
  const [page, setPage] = React.useState<PageKey>('dashboard');
  return (
    <TooltipProvider delayDuration={200}>
      <AppProvider page={page} setPage={setPage}>
        <Shell />
      </AppProvider>
    </TooltipProvider>
  );
}

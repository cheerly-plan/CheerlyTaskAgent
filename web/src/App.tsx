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
import {MappingsPage} from '@/pages/mappings-page';
import {ModelsPage} from '@/pages/models-page';
import {SettingsPage} from '@/pages/settings-page';
import {TasksPage} from '@/pages/tasks-page';
import {ReportOverlay} from '@/components/report-overlay';
import {Toaster} from '@/components/ui/sonner';
import {TooltipProvider} from '@/components/ui/tooltip';

function Shell() {
  const {conn, connText, openNewTask, page, setPage, reportView, closeReport} = useApp();

  const goPage = (next: PageKey) => {
    if (next !== 'report') closeReport();
    setPage(next);
  };

  // 报告打开时，背景仍是来源页（报告以叠加层呈现，不再整页替换）。
  // backFrom 为空或异常时兜底到任务中心，避免背景空白。
  const background: PageKey =
    page === 'report' && reportView.backFrom && reportView.backFrom !== 'report'
      ? reportView.backFrom
      : page === 'report'
        ? 'tasks'
        : page;

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
        <div className="relative flex min-h-0 flex-1">
          <main className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-5 lg:px-6">
            {background === 'dashboard' ? <DashboardPage /> : null}
            {background === 'tasks' ? <TasksPage /> : null}
            {background === 'history' ? <HistoryPage /> : null}
            {background === 'mapping' ? <MappingsPage /> : null}
            {background === 'sources' ? <DataSourcesPage /> : null}
            {background === 'models' ? <ModelsPage /> : null}
            {background === 'settings' ? <SettingsPage /> : null}
          </main>
        </div>
      </div>

      {/* 报告以全屏浮层呈现，盖住顶栏与侧栏；背景仍渲染来源页 */}
      {page === 'report' ? <ReportOverlay /> : null}

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

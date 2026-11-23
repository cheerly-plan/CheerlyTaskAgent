import * as React from 'react';

import {useApp} from '@/app-context';
import {ReportPage} from '@/pages/report-page';
import {cn} from '@/lib/utils';

export type ReportLayout = 'drawer' | 'split' | 'focus';

/**
 * 报告叠加层：来源页（任务中心 / 历史记录）照常留在背景，报告以全屏浮层覆盖整个视口
 * （含顶栏与侧栏）。
 *
 * 三档布局与原型一致：抽屉（背景虚化）/ 分屏（与列表并排）/ 专注（占满主区）。
 * 档位是会话内临时偏好，切换报告不重置；关闭报告时随组件卸载。
 */
export function ReportOverlay() {
  const {closeReport} = useApp();
  const [layout, setLayout] = React.useState<ReportLayout>('drawer');

  return (
    <div className="fixed inset-0 z-50 flex" data-layout={layout}>
      {/* 背景：抽屉虚化、分屏轻微压暗、专注完全遮住。 */}
      <div
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute inset-0 transition-all duration-300',
          layout === 'focus'
            ? 'bg-background'
            : layout === 'split'
              ? 'bg-background/10 backdrop-blur-[1px]'
              : 'bg-[rgba(28,28,52,0.18)] backdrop-blur-[3px]',
        )}
      />

      <section
        className={cn(
          'relative ml-auto flex h-full flex-col overflow-hidden border-l border-border/70 bg-card/90 shadow-[-20px_0_60px_-18px_rgba(40,38,100,0.28)] backdrop-blur-2xl transition-[width] duration-300',
          layout === 'drawer' && 'w-[min(1040px,94%)] rounded-l-[18px]',
          layout === 'split' && 'w-[74%] rounded-l-[18px]',
          layout === 'focus' && 'w-full',
        )}
        aria-label="报告面板"
      >
        <ReportPage layout={layout} onLayoutChange={setLayout} onClose={closeReport} />
      </section>
    </div>
  );
}

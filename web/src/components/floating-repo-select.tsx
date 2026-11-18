import * as React from 'react';
import {createPortal} from 'react-dom';
import {Check, ChevronDown, X} from 'lucide-react';

import {Badge} from '@/components/ui/badge';
import {cn} from '@/lib/utils';

export interface FloatingRepoOption {
  id: string;
  name: string;
  path?: string;
  /** 选项右侧附加文本（如关联的本地仓库名），显示在 name 之后。 */
  trailing?: string;
}

interface FloatingRepoSelectProps {
  /** 可勾选的本地仓库。 */
  options: FloatingRepoOption[];
  /** 已勾选的 id 列表。 */
  selected: string[];
  onToggle: (id: string) => void;
  /** 未勾选时的占位文案。 */
  placeholder?: string;
  /** 空列表提示。 */
  emptyText?: string;
  /** 触发按钮尺寸：sm 用于项目行内，default 用于表单。 */
  size?: 'sm' | 'default';
  /** 浮层最大高度（px）。 */
  maxHeight?: number;
  /** 只读模式：不显示复选框、不可勾选，仅展示（选项右侧可带 trailing 文本）。 */
  readOnly?: boolean;
  /** 提供后，每个选项右侧渲染「×」删除按钮，点击回调（通常用于只读展示态下删除条目）。 */
  onRemove?: (id: string) => void;
  /** 选项较多时提供搜索框（按名称 / 附加文本过滤）。 */
  searchable?: boolean;
  /** 提供后浮层底部显示「全选」快捷操作。 */
  onSelectAll?: () => void;
  /** 提供后浮层底部显示「清空」快捷操作。 */
  onClear?: () => void;
}

/**
 * 悬浮式本地仓库多选。浮层通过 Portal 渲染到 document.body 并以 fixed 定位，
 * 因此不会撑开所在弹窗、也不受弹窗 overflow-hidden 裁切（Radix Dialog z-50，浮层 z-[60]）。
 * 点击外部 / Esc 关闭；位置在触发按钮下方，空间不足时翻到上方。
 */
export function FloatingRepoSelect({
  options,
  selected,
  onToggle,
  placeholder = '未关联仓库',
  emptyText = '暂无可选项',
  size = 'default',
  maxHeight = 260,
  readOnly = false,
  onRemove,
  searchable = false,
  onSelectAll,
  onClear,
}: FloatingRepoSelectProps) {
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const popRef = React.useRef<HTMLDivElement>(null);
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [pos, setPos] = React.useState<{top: number; left: number; width: number; above: boolean}>({
    top: 0, left: 0, width: 0, above: false,
  });

  const selectedSet = React.useMemo(() => new Set(selected), [selected]);
  const keyword = query.trim().toLowerCase();
  const visibleOptions = keyword
    ? options.filter((option) =>
        `${option.name} ${option.trailing ?? ''}`.toLowerCase().includes(keyword),
      )
    : options;
  const selectedNames = options
    .filter((o) => selectedSet.has(o.id))
    .map((o) => o.name);
  const summary = selectedNames.length === 0
    ? placeholder
    : selectedNames.length <= 2
      ? selectedNames.join('、')
      : `${selectedNames.slice(0, 2).join('、')} 等 ${selectedNames.length} 个`;

  // 计算浮层位置：与触发按钮同宽，优先放下方，视口空间不足则放上方。
  const place = React.useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 4;
    const estimated = Math.min(maxHeight, 80 + options.length * 30);
    const roomBelow = window.innerHeight - r.bottom - gap;
    const above = roomBelow < Math.min(estimated, 160) && r.top > roomBelow;
    const top = above ? Math.max(8, r.top - gap - Math.min(estimated, maxHeight)) : r.bottom + gap;
    setPos({
      top,
      left: Math.max(8, Math.min(r.left, window.innerWidth - r.width - 8)),
      width: r.width,
      above,
    });
  }, [maxHeight, options.length]);

  // 打开时定位；滚动/缩放时跟随或关闭。
  React.useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    place();
    const onScroll = () => place();
    const onResize = () => place();
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  }, [open, place]);

  // 点击外部 / Esc 关闭。
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || popRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  // 浮层节点上的原生监听：阻止 pointerdown 冒泡到 document，
  // 否则 Radix Dialog 的 outside-dismiss 会把整层弹窗一起关掉。
  // 注意：只拦 pointerdown，不能拦 click —— React 18 把事件委托挂在
  // portal 容器（document.body）上，若 stopPropagation 掉 click 冒泡，
  // 选项按钮的 onClick 就永远收不到事件，导致「勾选无效」。
  React.useEffect(() => {
    const el = popRef.current;
    if (!open || !el) return;
    const stopPointerDown = (event: Event) => event.stopPropagation();
    el.addEventListener('pointerdown', stopPointerDown);
    return () => {
      el.removeEventListener('pointerdown', stopPointerDown);
    };
  }, [open, pos]);

  const trigger = (
    <button
      ref={triggerRef}
      type="button"
      onClick={() => setOpen((prev) => !prev)}
      className={cn(
        'flex w-full items-center gap-2 rounded-lg border border-border bg-card text-left transition-colors hover:border-primary/30',
        size === 'sm' ? 'px-2 py-1 text-[11.5px]' : 'px-3 py-2 text-[12px]',
        open && 'border-primary ring-[3px] ring-ring/20',
      )}
    >
      <span className={cn('min-w-0 flex-1 truncate', selected.length ? 'text-foreground' : 'text-faint')}>
        {summary}
      </span>
      <Badge variant={selected.length ? 'success' : 'info'}>{selected.length}</Badge>
      <ChevronDown
        className={cn('size-4 shrink-0 text-faint transition-transform', open && 'rotate-180')}
      />
    </button>
  );

  const popover = open && createPortal(
    <div
      ref={popRef}
      style={{position: 'fixed', top: pos.top, left: pos.left, width: pos.width, maxHeight, zIndex: 60, pointerEvents: 'auto'}}
      // 只拦 pointerdown（防 Radix Dialog outside-dismiss 关弹窗）；
      // 不拦 click，否则会阻断 React 委托在 portal 容器上的 onClick。
      onPointerDown={(event) => event.stopPropagation()}
      className="scrollbar-thin overflow-y-auto rounded-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-lg"
    >
      {searchable && options.length > 4 ? (
        <div className="sticky -top-1.5 z-10 -mx-1.5 -mt-1.5 mb-1 border-b border-border bg-popover px-1.5 py-1.5">
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onPointerDown={(event) => event.stopPropagation()}
            placeholder="请输入项目名搜索"
            className="h-7 w-full rounded-md border border-border bg-card px-2 text-[11.5px] outline-none placeholder:text-faint focus:border-primary"
          />
        </div>
      ) : null}
      {options.length === 0 ? (
        <p className="px-1.5 py-1 text-[11px] text-faint">{emptyText}</p>
      ) : visibleOptions.length === 0 ? (
        <p className="px-1.5 py-1 text-[11px] text-faint">没有匹配的项目</p>
      ) : (
        visibleOptions.map((option) => {
          const checked = selectedSet.has(option.id);
          return (
            <div
              key={option.id}
              role="button"
              tabIndex={readOnly && !onRemove ? -1 : 0}
              title={option.path}
              onClick={() => !readOnly && onToggle(option.id)}
              className={cn(
                'flex w-full min-w-0 items-center gap-2 rounded px-2 py-1.5 text-left text-[12px]',
                readOnly
                  ? 'cursor-default'
                  : cn('cursor-pointer transition-colors', checked ? 'bg-primary/10 text-foreground' : 'hover:bg-secondary/60'),
              )}
            >
              {!readOnly && (
                <span
                  className={cn(
                    'flex size-4 shrink-0 items-center justify-center rounded border',
                    checked ? 'border-primary bg-primary text-white' : 'border-border',
                  )}
                >
                  {checked ? <Check className="size-3" /> : null}
                </span>
              )}
              <span className="min-w-0 flex-1 truncate">{option.name}</span>
              {option.trailing && (
                <span className="max-w-[45%] shrink-0 truncate text-[11px] text-faint">{option.trailing}</span>
              )}
              {option.path && !option.trailing && (
                <span className="max-w-[45%] truncate text-[11px] text-faint">{option.path}</span>
              )}
              {onRemove && (
                <button
                  type="button"
                  tabIndex={-1}
                  title="删除"
                  aria-label={`删除 ${option.name}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onRemove(option.id);
                  }}
                  className="ml-1 flex size-4 shrink-0 items-center justify-center rounded text-faint transition-colors hover:bg-secondary hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          );
        })
      )}
      {onSelectAll || onClear ? (
        <div className="mt-1 flex items-center justify-between border-t border-border px-1.5 pt-1.5 text-[11px]">
          <span className="text-faint">已选 {selected.length} / {options.length}</span>
          <span className="flex items-center gap-2">
            {onSelectAll ? (
              <button type="button" className="text-primary hover:underline" onClick={onSelectAll}>
                全选
              </button>
            ) : null}
            {onClear ? (
              <button type="button" className="text-faint hover:text-foreground" onClick={onClear}>
                清空
              </button>
            ) : null}
          </span>
        </div>
      ) : null}
    </div>,
    document.body,
  );

  return (
    <>
      {trigger}
      {popover}
    </>
  );
}

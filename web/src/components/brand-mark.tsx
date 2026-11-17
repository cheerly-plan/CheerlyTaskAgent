import {cn} from '@/lib/utils';

/** 顶栏品牌标：渐变圆角方块 + 折线图形，替代原 favicon 占位。 */
export function BrandMark({size = 28, className}: {size?: number; className?: string}) {
  return (
    <div
      className={cn(
        'relative grid shrink-0 place-items-center overflow-hidden rounded-[9px] bg-gradient-to-br from-[#6d65d6] via-[#5b54c4] to-[#4a43b0] text-white shadow-[inset_0_1px_0_rgb(255_255_255/.25),0_2px_6px_rgb(91_84_196/.35)]',
        className,
      )}
      style={{width: size, height: size}}
    >
      <svg
        viewBox="0 0 24 24"
        width={size * 0.6}
        height={size * 0.6}
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M4 17.5 9 9.5l3.5 4.5L15 11l5 6.5" />
        <path d="M4 7h6" opacity=".55" />
      </svg>
      <span className="pointer-events-none absolute -right-2 -top-2 size-5 rounded-full bg-white/20 blur-[6px]" />
    </div>
  );
}
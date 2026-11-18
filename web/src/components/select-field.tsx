import * as React from 'react';

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {cn} from '@/lib/utils';

/**
 * Radix Select 不允许用空字符串作为选项值（空串代表「未选择」），
 * 后端里「全部 / 跟随全局」这类选项就用哨兵值转一道，业务层仍然拿 '' 。
 */
const EMPTY_VALUE = '__empty__';

export interface SelectFieldOption {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
  /** 分组标题；仅当选项里出现多个分组时才渲染标题，避免单项分组成为噪音。 */
  group?: string;
}

interface SelectFieldProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectFieldOption[];
  placeholder?: string;
  className?: string;
  size?: 'sm' | 'default';
  disabled?: boolean;
  title?: string;
  id?: string;
  /** 下拉展开回调：用于「首次打开时才探测」这类惰性动作。 */
  onOpenChange?: (open: boolean) => void;
}

export function SelectField({
  value,
  onChange,
  options,
  placeholder = '请选择',
  className,
  size = 'default',
  disabled,
  title,
  id,
  onOpenChange,
}: SelectFieldProps) {
  const current = options.find((option) => option.value === value);
  const groups = new Set(options.map((option) => option.group).filter(Boolean));
  const showGroups = groups.size > 1;
  const blocks: {group?: string; items: SelectFieldOption[]}[] = [];
  for (const option of options) {
    const last = blocks[blocks.length - 1];
    if (last && last.group === option.group) last.items.push(option);
    else blocks.push({group: option.group, items: [option]});
  }
  return (
    <Select
      value={value === '' ? EMPTY_VALUE : value}
      onValueChange={(next) => onChange(next === EMPTY_VALUE ? '' : next)}
      onOpenChange={onOpenChange}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        title={title}
        className={cn(size === 'sm' && 'h-8 rounded-md px-2 text-xs', className)}
      >
        <SelectValue placeholder={placeholder}>
          {current ? <span className="truncate">{current.label}</span> : null}
        </SelectValue>
      </SelectTrigger>
      <SelectContent className="max-h-[min(320px,var(--radix-select-content-available-height))]">
        {blocks.map((block) => {
          const items = block.items.map((option) => (
            <SelectItem
              key={option.value === '' ? EMPTY_VALUE : option.value}
              value={option.value === '' ? EMPTY_VALUE : option.value}
              disabled={option.disabled}
              className="text-[13px]"
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{option.label}</span>
                {option.hint ? (
                  <span className="truncate text-[11px] text-faint">{option.hint}</span>
                ) : null}
              </span>
            </SelectItem>
          ));
          if (!showGroups || !block.group) return <React.Fragment key={`g-${block.group ?? 'none'}-${block.items[0].value}`}>{items}</React.Fragment>;
          return (
            <SelectGroup key={`group-${block.group}`}>
              <SelectLabel className="px-2 pt-1.5 pb-1 text-[11px] font-medium tracking-wide text-faint">
                {block.group}
              </SelectLabel>
              {items}
            </SelectGroup>
          );
        })}
      </SelectContent>
    </Select>
  );
}

/** 带标题的表单字段，等价于原页面的 .field / .field-label。 */
export function Field({
  label,
  hint,
  htmlFor,
  children,
  className,
  labelExtra,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
  /** label 右侧的附加内容（如快捷链接），与标题同一行右对齐。 */
  labelExtra?: React.ReactNode;
}) {
  return (
    <div className={cn('mb-3.5', className)}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <label
          htmlFor={htmlFor}
          className="block text-xs font-semibold tracking-wide text-foreground/75"
        >
          {label}
        </label>
        {labelExtra}
      </div>
      {children}
      {hint ? <p className="mt-1 text-[11.5px] text-faint">{hint}</p> : null}
    </div>
  );
}
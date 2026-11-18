import * as React from 'react';

import {
  Select,
  SelectContent,
  SelectItem,
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
        {options.map((option) => (
          <SelectItem
            key={option.value === '' ? EMPTY_VALUE : option.value}
            value={option.value === '' ? EMPTY_VALUE : option.value}
            disabled={option.disabled}
            className="text-[13px]"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{option.label}</span>
              {option.hint ? <span className="truncate text-[11px] text-faint">{option.hint}</span> : null}
            </span>
          </SelectItem>
        ))}
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
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('mb-3.5', className)}>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-xs font-semibold tracking-wide text-foreground/75"
      >
        {label}
      </label>
      {children}
      {hint ? <p className="mt-1 text-[11.5px] text-faint">{hint}</p> : null}
    </div>
  );
}
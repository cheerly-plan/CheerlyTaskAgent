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

const EMPTY_VALUE = '__empty__';

export interface SelectFieldOption {
  value: string;
  label: string;
  disabled?: boolean;
  group?: string;
}

interface SelectFieldProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectFieldOption[];
  className?: string;
  size?: 'sm' | 'default';
  disabled?: boolean;
  id?: string;
  onOpenChange?: (open: boolean) => void;
}

export function SelectField({
  value,
  onChange,
  options,
  className,
  size = 'default',
  disabled,
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
        className={cn(size === 'sm' && 'h-8 rounded-md px-2 text-xs', className)}
      >
        <SelectValue>
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

export function Field({
  label,
  htmlFor,
  children,
  className,
  labelExtra,
}: {
  label: React.ReactNode;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
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
    </div>
  );
}
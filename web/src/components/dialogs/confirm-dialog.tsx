import {AlertTriangle, Check} from 'lucide-react';

import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface ConfirmDialogProps {
  open: boolean;
  message: string;
  onResolve: (confirmed: boolean) => void;
  confirmLabel?: string;
}

export function ConfirmDialog({open, message, onResolve, confirmLabel = '确认执行'}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onResolve(false);
      }}
    >
      <DialogContent
        className="max-w-[460px]"
        onEscapeKeyDown={() => onResolve(false)}
        onPointerDownOutside={() => onResolve(false)}
      >
        <DialogHeader>
          <DialogTitle>请确认</DialogTitle>
        </DialogHeader>

        <DialogBody>
          <div className="flex items-start gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-warning-muted text-warning-foreground">
              <AlertTriangle className="size-[18px]" />
            </span>
            <p className="pt-1.5 text-[13px] leading-relaxed text-foreground/85">{message}</p>
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onResolve(false)}>
            取消
          </Button>
          <Button variant="destructive" onClick={() => onResolve(true)}>
            <Check />
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
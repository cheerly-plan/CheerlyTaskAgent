import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-px text-[11.5px] font-medium leading-[18px] transition-colors focus:outline-none [&_svg]:size-3",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80",
        "destructive-muted":
          "border-transparent bg-destructive-muted text-destructive hover:bg-destructive-muted/80",
        outline: "border-border bg-card text-foreground/75",
        muted: "border-transparent bg-secondary text-muted-foreground",
        accent: "border-transparent bg-accent text-accent-foreground",
        success: "border-transparent bg-success-muted text-success-foreground",
        warning: "border-transparent bg-warning-muted text-warning-foreground",
        info: "border-transparent bg-info-muted text-info-foreground",
        purple: "border-transparent bg-[#f2edfd] text-[#6d3fc0]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }

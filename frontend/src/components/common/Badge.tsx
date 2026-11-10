import { cva } from 'class-variance-authority';
import { RowStatus } from '@/api/types';

// Inline utility to merge classes (simulating cn)
function classNames(...inputs: (string | undefined | null | false)[]) {
  return inputs.filter(Boolean).join(' ');
}

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset transition-all",
  {
    variants: {
      status: {
        Idle: "bg-gray-400/10 text-gray-400 ring-gray-400/20",
        Queued: "bg-blue-400/10 text-blue-400 ring-blue-400/20 animate-pulse",
        Generating: "bg-amber-400/10 text-amber-400 ring-amber-400/20 animate-pulse",
        Completed: "bg-emerald-400/10 text-emerald-400 ring-emerald-400/20",
        Error: "bg-red-400/10 text-red-400 ring-red-400/20",
        Filtered: "bg-fuchsia-400/10 text-fuchsia-300 ring-fuchsia-400/20",
        Cancelled: "bg-gray-400/10 text-gray-400 ring-gray-400/20",
        Interrupted: "bg-orange-400/10 text-orange-300 ring-orange-400/20",
      },
    },
    defaultVariants: {
      status: "Idle",
    },
  }
);

const allowedStatuses: RowStatus[] = ["Idle", "Queued", "Generating", "Completed", "Error", "Filtered", "Cancelled", "Interrupted"];

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  status: RowStatus;
}

export function Badge({ className, status, ...props }: BadgeProps) {
  const variant = allowedStatuses.includes(status) ? status : "Idle";
  
  return (
    <span className={classNames(badgeVariants({ status: variant }), className)} {...props}>
      {status}
    </span>
  );
}

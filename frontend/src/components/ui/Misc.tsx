import { Loader2 } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const tones: Record<Tone, string> = {
  neutral: "bg-raised text-muted border-line",
  accent: "bg-accent/10 text-accent border-accent/20",
  success: "bg-success/10 text-success border-success/20",
  warning: "bg-warning/10 text-warning border-warning/20",
  danger: "bg-danger/10 text-danger border-danger/20",
  info: "bg-info/10 text-info border-info/20",
};

export function Badge({ tone = "neutral", dot, className, children }: { tone?: Tone; dot?: boolean; className?: string; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 whitespace-nowrap rounded-[5px] border px-1.5 text-2xs font-medium",
        tones[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

interface ProgressProps {
  value: number | null;
  tone?: "accent" | "success" | "warning" | "danger" | "neutral";
  active?: boolean;
  className?: string;
  label?: string;
}

const bars = {
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  neutral: "bg-subtle",
};

/** Determinate bar; `value === null` with `active` shows an indeterminate sweep. */
export function ProgressBar({ value, tone = "accent", active, className, label }: ProgressProps) {
  const indeterminate = value === null && active;
  return (
    <div
      role="progressbar"
      aria-label={label ?? "Progress"}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value === null ? undefined : Math.round(value)}
      className={cn("h-1 w-full overflow-hidden rounded-full bg-line", className)}
    >
      {indeterminate ? (
        <div className={cn("h-full w-2/5 animate-indeterminate rounded-full", bars[tone])} />
      ) : (
        <div
          className={cn("h-full rounded-full transition-[width] duration-500 ease-out", bars[tone])}
          style={{ width: `${value ?? 0}%` }}
        />
      )}
    </div>
  );
}

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-line bg-surface", className)} {...rest} />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-subtle", className)} aria-label="Loading" />;
}

export function PageLoader() {
  return (
    <div className="flex h-48 items-center justify-center">
      <Spinner className="h-5 w-5" />
    </div>
  );
}

interface EmptyProps {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-14 text-center", className)}>
      {icon && (
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-raised text-subtle">
          {icon}
        </div>
      )}
      <p className="text-[13px] font-medium text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm text-xs leading-5 text-subtle">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorNote({ children, className }: { children: ReactNode; className?: string }) {
  if (!children) return null;
  return (
    <div role="alert" className={cn("rounded border border-danger/25 bg-danger/10 px-3 py-2 text-xs leading-5 text-danger", className)}>
      {children}
    </div>
  );
}

export function Notice({ tone = "info", children, className }: { tone?: "info" | "warning" | "success"; children: ReactNode; className?: string }) {
  const style = { info: tones.info, warning: tones.warning, success: tones.success }[tone];
  return <div className={cn("rounded border px-3 py-2 text-xs leading-5", style, className)}>{children}</div>;
}

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-[-0.01em] text-fg">{title}</h1>
        {description && <p className="mt-1 text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

interface SegmentedProps<T extends string | number> {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; disabled?: boolean }[];
  label: string;
  className?: string;
}

export function Segmented<T extends string | number>({ value, onChange, options, label, className }: SegmentedProps<T>) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex rounded border border-line bg-canvas p-0.5", className)}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            "h-6 rounded-[4px] px-2.5 text-xs font-medium transition-colors disabled:opacity-40",
            option.value === value ? "bg-overlay text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const button = "h-7 rounded border border-line px-2.5 text-xs text-muted transition-colors hover:bg-raised hover:text-fg disabled:pointer-events-none disabled:opacity-40";
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5 text-xs text-subtle">
      <span className="tabular">
        {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-1.5">
        <button type="button" className={button} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </button>
        <span className="tabular px-1">
          {page} / {pages}
        </span>
        <button type="button" className={button} disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}

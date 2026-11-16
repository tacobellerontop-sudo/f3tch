import { ChevronDown } from "lucide-react";
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/cn";

const control =
  "w-full rounded border border-line bg-canvas px-2.5 text-[13px] text-fg placeholder:text-subtle " +
  "transition-colors hover:border-line-strong focus:border-accent/70 focus:outline-none focus:ring-2 " +
  "focus:ring-accent/20 disabled:cursor-not-allowed disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(control, "h-8", className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cn(control, "py-2 leading-5", className)} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <span className={cn("relative inline-flex", className)}>
      <select ref={ref} className={cn(control, "h-8 appearance-none pr-7")} {...rest}>
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
    </span>
  );
});

interface FieldProps {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  className?: string;
  children: (props: { id: string; "aria-describedby"?: string }) => ReactNode;
}

/** Label + control + hint, wired together for accessibility. */
export function Field({ label, hint, error, className, children }: FieldProps) {
  const id = useId();
  const describedBy = hint || error ? `${id}-hint` : undefined;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy })}
      {(error || hint) && (
        <p id={describedBy} className={cn("text-xs leading-4", error ? "text-danger" : "text-subtle")}>
          {error || hint}
        </p>
      )}
    </div>
  );
}

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}

export function Switch({ checked, onChange, label, description, disabled }: SwitchProps) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <label htmlFor={id} className="min-w-0 cursor-pointer">
        <span className="block text-[13px] font-medium text-fg">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-subtle">{description}</span>}
      </label>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative mt-0.5 h-5 w-9 shrink-0 rounded-full border transition-colors disabled:opacity-50",
          checked ? "border-accent bg-accent" : "border-line-strong bg-raised",
        )}
      >
        <span
          className={cn(
            "absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full transition-all",
            checked ? "left-[18px] bg-accent-fg" : "left-0.5 bg-muted",
          )}
        />
      </button>
    </div>
  );
}

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { className, label, ...rest },
  ref,
) {
  const box = (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        "h-3.5 w-3.5 shrink-0 cursor-pointer rounded-[4px] border border-line-strong bg-canvas accent-[rgb(var(--accent))]",
        className,
      )}
      {...rest}
    />
  );
  if (!label) return box;
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-fg">
      {box}
      {label}
    </label>
  );
});

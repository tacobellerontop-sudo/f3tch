import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface MenuItem {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  separatorBefore?: boolean;
}

interface MenuProps {
  trigger: (props: { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu" }) => ReactNode;
  items: MenuItem[];
  align?: "left" | "right";
  placement?: "bottom" | "top";
  className?: string;
}

/** Small dropdown menu with keyboard support. */
export function Menu({ trigger, items, align = "right", placement = "bottom", className }: MenuProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];
        if (buttons.length === 0) return;
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "ArrowDown" ? (index + 1) % buttons.length : (index - 1 + buttons.length) % buttons.length;
        buttons[next].focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    list.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className={cn("relative inline-flex", className)}>
      {trigger({ onClick: () => setOpen((value) => !value), "aria-expanded": open, "aria-haspopup": "menu" })}
      {open && (
        <div
          ref={list}
          role="menu"
          className={cn(
            "absolute z-40 min-w-[11rem] animate-pop-in rounded-md border border-line bg-overlay p-1 shadow-pop",
            align === "right" ? "right-0" : "left-0",
            placement === "bottom" ? "top-full mt-1" : "bottom-full mb-1",
          )}
        >
          {items.map((item) => (
            <div key={item.label}>
              {item.separatorBefore && <div className="my-1 h-px bg-line" />}
              <button
                type="button"
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
                className={cn(
                  "flex h-7 w-full items-center gap-2 rounded-[5px] px-2 text-left text-[13px] transition-colors",
                  "focus:bg-raised focus:outline-none disabled:pointer-events-none disabled:opacity-40",
                  item.danger ? "text-danger hover:bg-danger/10" : "text-fg hover:bg-raised",
                )}
              >
                {item.icon && <span className="flex h-3.5 w-3.5 items-center justify-center text-current opacity-80 [&>svg]:h-3.5 [&>svg]:w-3.5">{item.icon}</span>}
                {item.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

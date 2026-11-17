import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import type { ThemeChoice } from "./types";

const STORAGE_KEY = "fetch.theme";

interface ThemeValue {
  /** What the user picked ("system" follows the OS). */
  choice: ThemeChoice;
  /** What is actually applied. */
  resolved: "dark" | "light";
  setChoice: (choice: ThemeChoice) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside <ThemeProvider>");
  return value;
}

function systemPrefersLight(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: light)").matches;
}

function readStored(): ThemeChoice | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "dark" || value === "light" || value === "system" ? value : null;
  } catch {
    return null;
  }
}

interface Props {
  /** Preference saved on the account, if any. */
  accountChoice?: ThemeChoice | "";
  /** Instance-wide default from Settings. */
  defaultChoice?: ThemeChoice;
  children: ReactNode;
}

export function ThemeProvider({ accountChoice, defaultChoice = "dark", children }: Props) {
  const [local, setLocal] = useState<ThemeChoice | null>(readStored);
  const [systemLight, setSystemLight] = useState(systemPrefersLight);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setSystemLight(media.matches);
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);

  const choice: ThemeChoice = local ?? (accountChoice || defaultChoice);
  const resolved = choice === "system" ? (systemLight ? "light" : "dark") : choice;

  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
  }, [resolved]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setLocal(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private browsing: the choice simply lasts for this page.
    }
  }, []);

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

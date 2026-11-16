import {
  Activity,
  ArrowDownToLine,
  ChevronsLeft,
  ChevronsRight,
  FolderClosed,
  LayoutDashboard,
  ListOrdered,
  LogOut,
  Menu as MenuIcon,
  Plug,
  Plus,
  Settings as SettingsIcon,
  Users,
  X,
} from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { AddDownloadDialog } from "@/components/downloads/AddDownloadDialog";
import { Button, Menu, Notice, ProgressBar } from "@/components/ui";
import { useAuth, useMe } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatBytes, initials } from "@/lib/format";
import { useConnectionStatus, type ConnectionStatus } from "@/lib/realtime";

const COLLAPSE_KEY = "fetch.sidebar.collapsed";

interface AddDownloadOptions {
  folder?: string;
  destination?: "private" | "shared";
}

const AddDownloadContext = createContext<(options?: AddDownloadOptions) => void>(() => undefined);

/** Open the add-download dialog from anywhere in the app. */
export function useAddDownload() {
  return useContext(AddDownloadContext);
}

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/downloads", label: "Downloads", icon: ArrowDownToLine },
  { to: "/queue", label: "Queue", icon: ListOrdered },
  { to: "/files", label: "Files", icon: FolderClosed },
  { to: "/providers", label: "Providers", icon: Plug },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/users", label: "Users", icon: Users, admin: true },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

const STATUS: Record<ConnectionStatus, { label: string; dot: string; hint: string }> = {
  online: { label: "Live", dot: "bg-success", hint: "Connected. Updates arrive in real time." },
  connecting: { label: "Connecting", dot: "bg-warning animate-pulse", hint: "Connecting to the server…" },
  reconnecting: { label: "Reconnecting", dot: "bg-warning animate-pulse", hint: "Connection lost. Reconnecting…" },
  offline: { label: "Offline", dot: "bg-danger", hint: "No live connection. Data refreshes periodically." },
};

function Logo({ name, compact }: { name: string; compact: boolean }) {
  return (
    <div className="flex items-center gap-2.5 overflow-hidden">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-accent">
        <ArrowDownToLine className="h-4 w-4" strokeWidth={2.4} />
      </span>
      {!compact && <span className="truncate text-sm font-semibold tracking-wide text-fg">{name}</span>}
    </div>
  );
}

function SidebarContent({ compact, onNavigate, onToggle }: { compact: boolean; onNavigate?: () => void; onToggle?: () => void }) {
  const { bootstrap, signOut } = useAuth();
  const me = useMe();
  const navigate = useNavigate();
  const status = useConnectionStatus();
  const openAdd = useAddDownload();
  const isAdmin = me.user.role === "admin";
  const { storage_bytes: used } = me.quota.usage;
  const limit = me.quota.limits.max_storage_bytes;
  const percent = limit > 0 ? Math.min(100, (used / limit) * 100) : null;
  const meta = STATUS[status];

  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-14 shrink-0 items-center border-b border-line", compact ? "justify-center px-2" : "justify-between px-4")}>
        <Logo name={bootstrap?.app_name ?? "FETCH"} compact={compact} />
        {onToggle && !compact && (
          <Button variant="ghost" size="icon-sm" onClick={onToggle} aria-label="Collapse sidebar">
            <ChevronsLeft className="h-4 w-4" />
          </Button>
        )}
      </div>

      <div className={cn("shrink-0 pt-3", compact ? "px-2" : "px-3")}>
        <Button
          variant="primary"
          className="w-full"
          size={compact ? "icon" : "md"}
          icon={<Plus className="h-4 w-4" />}
          aria-label="Add download"
          onClick={() => {
            onNavigate?.();
            openAdd();
          }}
        >
          {!compact && "Add download"}
        </Button>
      </div>

      <nav aria-label="Main" className={cn("flex-1 space-y-0.5 overflow-y-auto py-3", compact ? "px-2" : "px-3")}>
        {NAV.filter((item) => !item.admin || isAdmin).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            title={compact ? item.label : undefined}
            className={({ isActive }) =>
              cn(
                "group flex h-8 items-center gap-2.5 rounded text-[13px] font-medium transition-colors",
                compact ? "justify-center px-0" : "px-2.5",
                isActive ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg",
              )
            }
          >
            {({ isActive }) => (
              <>
                <item.icon className={cn("h-4 w-4 shrink-0", isActive ? "text-accent" : "text-subtle group-hover:text-muted")} />
                {!compact && <span className="truncate">{item.label}</span>}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className={cn("shrink-0 space-y-3 border-t border-line py-3", compact ? "px-2" : "px-3")}>
        {!compact && (
          <div className="px-1" title={limit > 0 ? `${formatBytes(used)} of ${formatBytes(limit)} used` : `${formatBytes(used)} used`}>
            <div className="mb-1.5 flex items-center justify-between text-2xs text-subtle">
              <span>Storage</span>
              <span className="tabular">
                {formatBytes(used)}
                {limit > 0 ? ` / ${formatBytes(limit, 0)}` : ""}
              </span>
            </div>
            <ProgressBar
              value={percent ?? 0}
              tone={percent !== null && percent > 90 ? "danger" : percent !== null && percent > 75 ? "warning" : "accent"}
              label="Storage used"
            />
          </div>
        )}

        <div
          className={cn("flex items-center gap-2 text-2xs text-subtle", compact ? "justify-center" : "px-1")}
          title={meta.hint}
          role="status"
          aria-label={`Server connection: ${meta.label}`}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
          {!compact && <span>{meta.label}</span>}
        </div>

        <Menu
          align="left"
          placement="top"
          className="w-full"
          items={[
            { label: "Account settings", icon: <SettingsIcon />, onSelect: () => { onNavigate?.(); navigate("/settings"); } },
            { label: "Sign out", icon: <LogOut />, onSelect: () => void signOut(), separatorBefore: true },
          ]}
          trigger={(props) => (
            <button
              type="button"
              {...props}
              aria-label="Account menu"
              className={cn(
                "flex w-full items-center gap-2.5 rounded p-1.5 text-left transition-colors hover:bg-raised",
                compact && "justify-center",
              )}
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line bg-raised text-2xs font-semibold text-muted">
                {initials(me.user.display_name || me.user.username)}
              </span>
              {!compact && (
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-medium text-fg">{me.user.display_name || me.user.username}</span>
                  <span className="block truncate text-2xs text-subtle">{isAdmin ? "Administrator" : "Member"}</span>
                </span>
              )}
            </button>
          )}
        />

        {onToggle && compact && (
          <Button variant="ghost" size="icon" className="w-full" onClick={onToggle} aria-label="Expand sidebar">
            <ChevronsRight className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}

export function AppShell({ banner }: { banner?: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [drawer, setDrawer] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [addOptions, setAddOptions] = useState<AddDownloadOptions>({});

  const toggle = useCallback(() => {
    setCollapsed((value) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, value ? "0" : "1");
      } catch {
        // Ignore: the preference just will not persist.
      }
      return !value;
    });
  }, []);

  useEffect(() => setDrawer(false), [location.pathname]);

  const openAdd = useCallback((options?: AddDownloadOptions) => {
    setAddOptions(options ?? {});
    setAddOpen(true);
  }, []);
  const closeAdd = useCallback(() => setAddOpen(false), []);
  const contextValue = useMemo(() => openAdd, [openAdd]);

  return (
    <AddDownloadContext.Provider value={contextValue}>
      <div className="flex h-screen overflow-hidden bg-canvas">
        <aside
          className={cn(
            "hidden shrink-0 border-r border-line bg-surface transition-[width] duration-150 md:block",
            collapsed ? "w-[52px]" : "w-60",
          )}
        >
          <SidebarContent compact={collapsed} onToggle={toggle} />
        </aside>

        {drawer && (
          <div className="fixed inset-0 z-40 md:hidden">
            <div className="absolute inset-0 animate-fade-in bg-black/60" onClick={() => setDrawer(false)} />
            <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-surface shadow-pop">
              <SidebarContent compact={false} onNavigate={() => setDrawer(false)} />
            </aside>
          </div>
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-12 shrink-0 items-center justify-between border-b border-line bg-surface px-3 md:hidden">
            <Button variant="ghost" size="icon" onClick={() => setDrawer((value) => !value)} aria-label="Open navigation">
              {drawer ? <X className="h-4 w-4" /> : <MenuIcon className="h-4 w-4" />}
            </Button>
            <Button variant="primary" size="sm" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => openAdd()}>
              Add
            </Button>
          </header>
          <main className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-[1280px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
              {me.user.must_change_password && (
                <Notice tone="warning" className="mb-5">
                  An administrator reset your password. Please choose a new one under Settings → Account.
                </Notice>
              )}
              {banner}
              <Outlet />
            </div>
          </main>
        </div>
      </div>
      <AddDownloadDialog
        open={addOpen}
        onClose={closeAdd}
        initialFolder={addOptions.folder}
        initialDestination={addOptions.destination}
      />
    </AddDownloadContext.Provider>
  );
}

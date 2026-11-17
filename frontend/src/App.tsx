import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ServerCrash } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { Button, EmptyState, PageLoader, ToastProvider } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth";
import { RealtimeProvider } from "@/lib/realtime";
import { ThemeProvider } from "@/lib/theme";
import { useUpdating } from "@/lib/updating";
import { ActivityPage } from "@/pages/Activity";
import { InvitePage, LoginPage, SetupPage } from "@/pages/Auth";
import { DownloadsPage } from "@/pages/Downloads";
import { FilesPage } from "@/pages/Files";
import { OverviewPage } from "@/pages/Overview";
import { ProvidersPage } from "@/pages/Providers";
import { QueuePage } from "@/pages/Queue";
import { SettingsPage } from "@/pages/Settings";
import { UpdatingScreen } from "@/pages/Updates";
import { UsersPage } from "@/pages/Users";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        retry: (count, error) => {
          // Client errors will not fix themselves; only retry transport problems.
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
          return count < 2;
        },
      },
    },
  });
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  return me?.user.role === "admin" ? <>{children}</> : <Navigate to="/" replace />;
}

function Routing() {
  const { bootstrap, me, loading, unreachable, sessionEnded, refresh } = useAuth();
  const location = useLocation();
  const updating = useUpdating();

  useEffect(() => {
    document.title = bootstrap?.app_name ?? "FETCH";
  }, [bootstrap?.app_name]);

  // An update was started from this tab: wait for the server to come back.
  if (updating) return <UpdatingScreen state={updating} />;
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <PageLoader />
      </div>
    );
  }
  if (unreachable || !bootstrap) {
    return (
      <div className="flex h-screen items-center justify-center">
        <EmptyState
          icon={<ServerCrash className="h-5 w-5" />}
          title="Cannot reach the FETCH server"
          description="Check that FETCH is running on the host computer and that you are connected to the same network or tailnet."
          action={<Button onClick={() => window.location.reload()}>Try again</Button>}
        />
      </div>
    );
  }

  return (
    <ThemeProvider accountChoice={me?.user.theme} defaultChoice={bootstrap.default_theme}>
      <RealtimeProvider
        enabled={me !== null}
        onSessionEnded={() => {
          sessionEnded();
          void refresh();
        }}
      >
        <Routes>
          {bootstrap.setup_required ? (
            <>
              <Route path="/setup" element={<SetupPage />} />
              <Route path="*" element={<Navigate to="/setup" replace />} />
            </>
          ) : me === null ? (
            <>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/invite/:token" element={<InvitePage />} />
              <Route path="/register" element={<InvitePage />} />
              <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname + location.search }} />} />
            </>
          ) : (
            <>
              <Route element={<AppShell />}>
                <Route index element={<OverviewPage />} />
                <Route path="downloads" element={<DownloadsPage />} />
                <Route path="queue" element={<QueuePage />} />
                <Route path="files" element={<FilesPage />} />
                <Route path="providers" element={<ProvidersPage />} />
                <Route path="activity" element={<ActivityPage />} />
                <Route path="users" element={<RequireAdmin><UsersPage /></RequireAdmin>} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="settings/:tab" element={<SettingsPage />} />
              </Route>
              <Route path="*" element={<Navigate to={(location.state as { from?: string } | null)?.from ?? "/"} replace />} />
            </>
          )}
        </Routes>
      </RealtimeProvider>
    </ThemeProvider>
  );
}

export function App() {
  const [client] = useState(createQueryClient);
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ToastProvider>
          <AuthProvider>
            <Routing />
          </AuthProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

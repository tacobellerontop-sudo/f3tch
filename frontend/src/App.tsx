import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ServerCrash } from "lucide-react";
import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { Button, EmptyState, PageLoader, ToastProvider } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { AppProvider, useApp } from "@/lib/app";
import { RealtimeProvider } from "@/lib/realtime";
import { ThemeProvider } from "@/lib/theme";
import { useUpdating } from "@/lib/updating";
import { ActivityPage } from "@/pages/Activity";
import { DownloadsPage } from "@/pages/Downloads";
import { FilesPage } from "@/pages/Files";
import { OverviewPage } from "@/pages/Overview";
import { ProvidersPage } from "@/pages/Providers";
import { QueuePage } from "@/pages/Queue";
import { SettingsPage } from "@/pages/Settings";
import { UpdatingScreen } from "@/pages/Updates";

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

function Routing() {
  const { bootstrap, loading, unreachable } = useApp();
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
          description="FETCH may still be starting, or it was stopped. Start FETCH again if this does not go away."
          action={<Button onClick={() => window.location.reload()}>Try again</Button>}
        />
      </div>
    );
  }

  return (
    <ThemeProvider accountChoice={bootstrap.theme} defaultChoice={bootstrap.default_theme}>
      <RealtimeProvider enabled>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<OverviewPage />} />
            <Route path="downloads" element={<DownloadsPage />} />
            <Route path="queue" element={<QueuePage />} />
            <Route path="files" element={<FilesPage />} />
            <Route path="providers" element={<ProvidersPage />} />
            <Route path="activity" element={<ActivityPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="settings/:tab" element={<SettingsPage />} />
          </Route>
          {/* Links from versions with accounts (sign-in, setup, invitations) lead home. */}
          <Route path="*" element={<Navigate to="/" replace />} />
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
          <AppProvider>
            <Routing />
          </AppProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

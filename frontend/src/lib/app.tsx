import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { api } from "./api";
import type { Bootstrap } from "./types";

// FETCH is a local application without accounts: the dashboard only needs to
// know that the server is there and what it is called.

interface AppValue {
  bootstrap: Bootstrap | undefined;
  loading: boolean;
  unreachable: boolean;
  refresh: () => Promise<void>;
}

const AppContext = createContext<AppValue | null>(null);

export function useApp(): AppValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp must be used inside <AppProvider>");
  return value;
}

/** Name, version and usage figures. Only inside the loaded dashboard. */
export function useBootstrap(): Bootstrap {
  const { bootstrap } = useApp();
  if (!bootstrap) throw new Error("useBootstrap requires a loaded dashboard");
  return bootstrap;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["bootstrap"],
    queryFn: () => api.get<Bootstrap>("/bootstrap"),
    staleTime: 30_000,
    retry: 2,
  });

  const refresh = useCallback(async () => {
    await client.invalidateQueries({ queryKey: ["bootstrap"] });
  }, [client]);

  const value = useMemo<AppValue>(
    () => ({ bootstrap: query.data, loading: query.isPending, unreachable: query.isError, refresh }),
    [query.data, query.isPending, query.isError, refresh],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

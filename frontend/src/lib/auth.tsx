import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";

import { api, ApiError, setCsrfToken, setUnauthorizedHandler } from "./api";
import type { Bootstrap, Me } from "./types";

interface AuthValue {
  bootstrap: Bootstrap | undefined;
  me: Me | null;
  loading: boolean;
  unreachable: boolean;
  /** Store the result of a successful login / setup / registration. */
  signIn: (me: Me) => void;
  signOut: () => Promise<void>;
  /** Forget the session locally (the server already ended it). */
  sessionEnded: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("useMe requires a signed-in user");
  return me;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();

  const bootstrapQuery = useQuery({
    queryKey: ["bootstrap"],
    queryFn: () => api.get<Bootstrap>("/bootstrap"),
    staleTime: 60_000,
    retry: 2,
  });

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<Me | null> => {
      try {
        const me = await api.get<Me>("/auth/me");
        setCsrfToken(me.csrf_token);
        return me;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 30_000,
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
  });

  const sessionEnded = useCallback(() => {
    setCsrfToken("");
    client.setQueryData(["me"], null);
    // Drop everything that belonged to the previous user.
    client.removeQueries({ predicate: (query) => !["me", "bootstrap"].includes(String(query.queryKey[0])) });
  }, [client]);

  useEffect(() => {
    setUnauthorizedHandler(sessionEnded);
    return () => setUnauthorizedHandler(null);
  }, [sessionEnded]);

  const signIn = useCallback(
    (me: Me) => {
      setCsrfToken(me.csrf_token);
      client.setQueryData(["me"], me);
      void client.invalidateQueries({ queryKey: ["bootstrap"] });
    },
    [client],
  );

  const signOut = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      // Even if the request fails the local session is discarded.
    }
    sessionEnded();
  }, [sessionEnded]);

  const refresh = useCallback(async () => {
    await client.invalidateQueries({ queryKey: ["me"] });
  }, [client]);

  const value = useMemo<AuthValue>(
    () => ({
      bootstrap: bootstrapQuery.data,
      me: meQuery.data ?? null,
      loading: bootstrapQuery.isPending || meQuery.isPending,
      unreachable: bootstrapQuery.isError || meQuery.isError,
      signIn,
      signOut,
      sessionEnded,
      refresh,
    }),
    [bootstrapQuery.data, bootstrapQuery.isPending, bootstrapQuery.isError, meQuery.data, meQuery.isPending,
      meQuery.isError, signIn, signOut, sessionEnded, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

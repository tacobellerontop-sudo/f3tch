import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { useMe } from "./auth";
import type { SettingsPayload } from "./types";

export { useInspect, useSubmit } from "./queries";

/**
 * Defaults for the add-download dialog. Regular users cannot read the
 * settings, so they get the built-in defaults and the server stays the judge.
 */
export function useSettingsDefaults(): { destination: "private" | "shared"; priority: number; sharedAllowed: boolean } {
  const me = useMe();
  const isAdmin = me.user.role === "admin";
  const settings = useQuery({
    queryKey: ["settings", "values"],
    queryFn: () => api.get<SettingsPayload>("/settings"),
    enabled: isAdmin,
    staleTime: 60_000,
  });
  const usage = useQuery({
    queryKey: ["files", "usage"],
    queryFn: () => api.get<{ shared: { enabled: boolean } }>("/files/usage"),
    staleTime: 60_000,
  });
  const values = settings.data?.settings;
  return {
    destination: values?.general.default_destination ?? "private",
    priority: values?.downloads.default_priority ?? 1,
    sharedAllowed: usage.data?.shared.enabled ?? false,
  };
}

import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import type { SettingsPayload } from "./types";

export { useInspect, useSubmit } from "./queries";

/** Defaults for the add-download dialog, from Settings. */
export function useSettingsDefaults(): { priority: number } {
  const settings = useQuery({
    queryKey: ["settings", "values"],
    queryFn: () => api.get<SettingsPayload>("/settings"),
    staleTime: 60_000,
  });
  return { priority: settings.data?.settings.downloads.default_priority ?? 1 };
}

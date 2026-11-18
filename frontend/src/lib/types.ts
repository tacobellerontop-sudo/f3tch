// Shapes of the FETCH API. Kept in one place so that the frontend and the
// backend (backend/fetch_app/schemas.py) can be compared side by side.

export type ThemeChoice = "dark" | "light" | "system";

export interface Usage {
  storage_bytes: number;
  /** 0 means no limit. */
  storage_limit_bytes: number;
  max_concurrent: number;
  active_downloads: number;
  open_downloads: number;
  downloads_today: number;
}

export interface Bootstrap {
  app_name: string;
  version: string;
  default_theme: ThemeChoice;
  language: string;
  /** The theme picked under Settings ("" until one is picked). */
  theme: "" | ThemeChoice;
  usage: Usage;
}

export type DownloadState =
  | "pending"
  | "queued"
  | "inspecting"
  | "waiting"
  | "downloading"
  | "paused"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled";

export interface Download {
  id: number;
  url: string;
  provider: string;
  filename: string;
  state: DownloadState;
  priority: number;
  position: number;
  destination: "private" | "shared";
  folder: string;
  total_bytes: number | null;
  downloaded_bytes: number;
  speed_bps: number;
  avg_speed_bps: number;
  eta_seconds: number | null;
  engine: string;
  attempts: number;
  max_attempts: number;
  error_code: string;
  error_message: string;
  wait_until: string | null;
  wait_reason: string;
  final_path: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** Set by the REST API when the download waits for its owner. */
  action_required?: "captcha" | "";
}

/** A provider CAPTCHA that the user completes themselves. FETCH never solves it. */
export interface CaptchaChallenge {
  download_id: number;
  provider: string;
  /** "image": type the characters here. "external": only works on the provider's site. */
  kind: "image" | "external";
  prompt: string;
  has_image: boolean;
  page_url: string;
  accepts_download_url: boolean;
  attempt: number;
  expires_at: string | null;
}

export interface Progress {
  id: number;
  downloaded_bytes: number;
  total_bytes: number | null;
  speed_bps: number;
  avg_speed_bps: number;
  eta_seconds: number | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface DownloadStats {
  by_state: Record<DownloadState, number>;
  active: number;
  queued: number;
  completed: number;
  failed: number;
  speed_bps: number;
  /** Optional on older servers. */
  schedule?: { enabled: boolean; open: boolean; next_change: string | null };
}

export interface LinkInfo {
  url: string;
  provider: string;
  provider_name: string;
  supported: boolean;
  filename: string;
  size: number | null;
  resumable: boolean | null;
  restriction: string;
  message: string;
  premium_only?: boolean;
  auth_mode?: string;
}

export interface SubmitResult {
  created: Download[];
  rejected: { url: string; provider: string; code: string; message: string }[];
}

export type DownloadAction = "pause" | "resume" | "cancel" | "retry";

export interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  type: string;
  modified_at: string;
  downloaded_at: string | null;
  provider: string;
  source_url: string;
}

export interface FileListing extends Page<FileItem> {
  scope: "private" | "shared";
  path: string;
}

export interface StorageUsage {
  private: { used_bytes: number; limit_bytes: number };
  /** Only holds files saved by versions that had a shared folder. */
  shared: { used_bytes: number };
  disk: { total: number; used: number; free: number };
}

export interface Capabilities {
  inspect: boolean;
  download: boolean;
  resume: boolean;
  anonymous: boolean;
  credentials: boolean;
  requires_credentials: boolean;
  folders: boolean;
  checksum: boolean;
  waits: boolean;
  captcha: boolean;
}

export interface CredentialField {
  name: string;
  label: string;
  kind: "text" | "password" | "textarea";
  required: boolean;
  help: string;
}

export type AuthMode = "anonymous" | "account";
export type AuthPreference = "auto" | AuthMode;

/** How FETCH signs in to a provider. Never contains credentials. */
export interface ProviderAuth {
  modes: AuthMode[];
  authentication_required: boolean;
  anonymous_available: boolean;
  account_configured: boolean;
  /** Result of the last account check; null when it was never checked. */
  account_valid: boolean | null;
  account_message: string;
  preference: AuthPreference;
  effective_mode: AuthMode | null;
  provider_available: boolean;
  status: AuthMode | "auth_required" | "unavailable";
  problem_code: string;
  message: string;
}

export interface Provider {
  id: string;
  name: string;
  description: string;
  homepage: string;
  capabilities: Capabilities;
  notes: string[];
  verification: string;
  enabled: boolean;
  configured: boolean;
  ready: boolean;
  auth_modes?: AuthMode[];
  auth?: ProviderAuth;
  credential_fields?: CredentialField[];
  health?: "ok" | "error" | "unknown" | "unavailable";
  health_message?: string;
  checked_at?: string | null;
  credential_fields_set?: string[];
  credentials_updated_at?: string | null;
}

export interface ProviderTestResult {
  ok: boolean;
  message: string;
  account: Record<string, unknown>;
}

export interface ProviderError {
  id: number;
  created_at: string;
  level: string;
  action: string;
  message: string;
  username: string;
  code: string;
}

export interface ActivityItem {
  id: number;
  created_at: string;
  category: string;
  action: string;
  level: "info" | "warning" | "error";
  message: string;
  target: string;
}

export interface AppSettings {
  general: {
    app_name: string;
    theme: ThemeChoice;
    language: string;
    default_destination: "private" | "shared";
    check_for_updates: boolean;
    close_to_tray: boolean;
    watch_clipboard: boolean;
  };
  downloads: {
    max_global_concurrent: number;
    speed_limit_kbps: number;
    retry_count: number;
    retry_delay_seconds: number;
    default_priority: number;
    use_aria2: boolean;
    aria2_path: string;
    aria2_min_size_mb: number;
    aria2_connections: number;
    connections_per_download: number;
    media_quality: "best" | "2160" | "1440" | "1080" | "720" | "480" | "audio";
    auto_extract: boolean;
    delete_after_extract: boolean;
    schedule_enabled: boolean;
    schedule_start: string;
    schedule_stop: string;
    schedule_days: number[];
  };
  storage: {
    storage_root: string;
    max_storage_gb: number;
    temp_cleanup_hours: number;
    auto_delete_days: number;
    history_retention_days: number;
  };
  network: {
    port: number;
  };
}

export interface SettingsPayload {
  settings: AppSettings;
  effective: {
    bind_address: string;
    port: number;
    storage_root: string;
    active_storage_root: string;
    config_file: string;
  };
  restart_required: string[];
  changed?: string[];
}

export interface HealthCheck {
  name: string;
  ok: boolean;
  detail: string;
  optional?: boolean;
}

export interface Health {
  status: "ok" | "degraded";
  checks: HealthCheck[];
  version: string;
  python: string;
  platform: string;
  uptime_seconds: number;
  listening: { bind_address: string; port: number };
  manager: { running: boolean; active_jobs: number; transferring: number; aggregate_speed_bps: number };
  downloads: Partial<Record<DownloadState, number>>;
  disk: { total: number; used: number; free: number };
  storage_used_bytes: number;
  realtime_clients: number;
  guard_proxy_denied: number;
  restart_required: string[];
}

export interface UpdateRun {
  state: "running" | "succeeded" | "failed" | "cancelled" | string;
  message: string;
  from_version: string;
  to_version: string;
  started_at: string;
  finished_at: string;
}

export interface UpdateStatus {
  current_version: string;
  repository: string;
  auto_check: boolean;
  checked_at: string | null;
  error: string;
  latest_version: string | null;
  update_available: boolean;
  release_name: string;
  notes: string;
  published_at: string | null;
  release_url: string;
  package_available: boolean;
  can_apply: boolean;
  apply_blocker: string;
  source: "git" | "release" | "exe";
  in_progress: boolean;
  last_run: UpdateRun | null;
}

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  message: string;
  [key: string]: unknown;
}

export interface ServerEvent {
  type: string;
  data: Record<string, unknown>;
}

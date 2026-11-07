export type Capability =
  | "chat"
  | "image"
  | "tts"
  | "asr"
  | "video"
  | "subtitle";
export interface Model {
  id: string;
  name: string;
  capability: Capability;
  adapter: string;
  operations: string[];
  enabled: boolean;
  config: Record<string, unknown>;
  provider_connection_id: string | null;
  device: string;
  memory_mb: number;
  concurrency: number;
  timeout_seconds: number;
  idle_seconds: number;
  state: string;
  active: number;
  last_used: number | null;
  last_error: string | null;
  created_at: number;
}
export interface User {
  id: string;
  username: string;
  role: "admin" | "user";
  enabled?: boolean;
  model_ids?: string[];
  created_at?: number;
}
export interface Key {
  id: string;
  name: string;
  prefix: string;
  model_ids: string[];
  rpm: number;
  expires_at: number | null;
  revoked: boolean;
  created_at: number;
  secret?: string;
}
export interface StoredFile {
  id: string;
  filename: string;
  media_type: string;
  size: number;
  created_at: number;
  expires_at: number;
  url: string;
  task_id: string | null;
}
export interface Task {
  id: string;
  model: string;
  capability: Capability;
  status: string;
  progress: number;
  result: Record<string, any> | null;
  error: { message: string; code: string } | null;
  cancel_requested: boolean;
  execution_metadata?: { cancellation?: string; upstream_task_id?: string };
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  expires_at: number;
  request_id: string;
}
export interface CallLog {
  id: string;
  username: string;
  key_id: string | null;
  model: string;
  capability: string;
  status: string;
  duration_ms: number;
  usage: Record<string, number>;
  error_code: string | null;
  request_id: string;
  created_at: number;
}
export interface Runtime {
  online: boolean;
  simulated: boolean;
  worker_id?: string;
  heartbeat?: number;
  loaded?: number;
  devices: {
    id: string;
    name: string;
    total_mb: number;
    used_mb: number;
    free_mb: number;
  }[];
}
export interface Overview {
  activity: number[];
  health: Record<string, boolean>;
  calls_today: number;
  success_rate: number;
  average_ms: number;
  pending_tasks: number;
  models_total: number;
  models_loaded: number;
  runtime: Runtime;
}

export interface ProviderPreset {
  id: string;
  name: string;
  base_url: string;
  docs_url: string;
  adapter: string;
}
export interface ProviderConnection {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  connect_timeout: number;
  read_timeout: number;
  has_api_key: boolean;
  created_at: number;
  updated_at: number;
  models: { id: string; name: string; state: string }[];
}

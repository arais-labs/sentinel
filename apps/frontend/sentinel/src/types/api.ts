export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthSetupStatus {
  configured: boolean;
  bootstrap_available: boolean;
}

export interface Session {
  id: string;
  user_id: string;
  agent_id: string | null;
  parent_session_id?: string | null;
  title: string | null;
  initial_prompt?: string | null;
  latest_system_prompt?: string | null;
  started_at: string;
  is_running: boolean;
  is_main?: boolean;
  has_unread?: boolean;
}

export interface SessionListResponse {
  items: Session[];
  total: number;
}

export interface SessionRuntimeAction {
  timestamp: string | null;
  action: string;
  details: Record<string, unknown>;
}

export interface SessionRuntimeStatus {
  session_id: string;
  runtime_exists: boolean;
  workspace_exists: boolean;
  active: boolean;
  active_pid: number | null;
  last_command: string | null;
  created_at: string | null;
  last_used_at: string | null;
  last_active_at: string | null;
  actions: SessionRuntimeAction[];
}

export interface SessionRuntimeFileEntry {
  name: string;
  path: string;
  kind: 'file' | 'directory';
  size_bytes: number | null;
  modified_at: string | null;
  is_git_root?: boolean;
  git_branch?: string | null;
  git_detached_head?: boolean;
}

export interface SessionRuntimeFilesResponse {
  session_id: string;
  runtime_exists: boolean;
  workspace_exists: boolean;
  path: string;
  parent_path: string | null;
  entries: SessionRuntimeFileEntry[];
  truncated: boolean;
}

export interface SessionRuntimeFilePreviewResponse {
  session_id: string;
  runtime_exists: boolean;
  workspace_exists: boolean;
  path: string;
  name: string;
  size_bytes: number;
  modified_at: string | null;
  content: string;
  truncated: boolean;
  max_bytes: number;
}

export interface SessionRuntimeGitRoot {
  root_path: string;
  branch: string | null;
  detached_head: boolean;
}

export interface SessionRuntimeGitRootsResponse {
  session_id: string;
  runtime_exists: boolean;
  workspace_exists: boolean;
  path: string;
  roots: SessionRuntimeGitRoot[];
}

export interface SessionRuntimeGitDiffResponse {
  session_id: string;
  runtime_exists: boolean;
  workspace_exists: boolean;
  path: string;
  git_root: string;
  branch: string | null;
  detached_head: boolean;
  base_ref: string;
  staged: boolean;
  context_lines: number;
  diff: string;
  truncated: boolean;
  max_bytes: number;
}

export interface SessionRuntimeGitChangedFile {
  path: string;
  status: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
}

export interface SessionRuntimeGitChangedFilesResponse {
  session_id: string;
  runtime_exists: boolean;
  workspace_exists: boolean;
  path: string;
  git_root: string;
  branch: string | null;
  detached_head: boolean;
  entries: SessionRuntimeGitChangedFile[];
  truncated: boolean;
}

export interface SessionRuntimeCleanupResponse {
  session_id: string;
  runtime_removed: boolean;
}

export interface SessionContextUsage {
  session_id: string;
  context_token_budget: number;
  estimated_context_tokens: number | null;
  estimated_context_percent: number | null;
  snapshot_created_at: string | null;
  source: string;
}

export interface Message {
  id: string;
  session_id: string;
  role: string;
  content: string;
  metadata: Record<string, unknown>;
  token_count: number | null;
  tool_call_id: string | null;
  tool_name: string | null;
  runtime_context_structured?: Record<string, unknown> | null;
  created_at: string;
}

export interface MessageAttachment {
  mime_type: string;
  base64: string;
  filename?: string | null;
  size_bytes?: number;
}

export interface MessageListResponse {
  items: Message[];
  has_more: boolean;
}

export interface ChatResponse {
  response: string;
  iterations: number;
  usage: {
    input_tokens: number;
    output_tokens: number;
  };
  error: string | null;
}

export interface ModelOption {
  label: string;
  description: string;
  tier: 'fast' | 'normal' | 'hard';
  primary_provider_id?: 'anthropic' | 'openai' | 'openai-codex' | 'gemini';
  primary_model_id?: string;
  fallback_providers?: Array<{
    provider_id: 'anthropic' | 'openai' | 'openai-codex' | 'gemini';
    model: string;
  }>;
  thinking_budget?: number;
  reasoning_effort?: string;
}

export interface ModelsResponse {
  models: ModelOption[];
  default_tier: 'fast' | 'normal' | 'hard' | null;
}

export interface AgentModeOption {
  id: string;
  label: string;
  description: string;
}

export interface AgentModesResponse {
  items: AgentModeOption[];
  default_mode: string;
}

export interface SubAgentTask {
  id: string;
  session_id: string;
  name: string;
  scope: string | null;
  max_steps: number;
  status: string;
  allowed_tools: string[];
  turns_used: number;
  grace_turns_used?: number;
  tokens_used: number;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface SubAgentTaskListResponse {
  items: SubAgentTask[];
  total: number;
}

export interface MemoryEntry {
  id: string;
  content: string;
  title: string | null;
  summary: string | null;
  category: string;
  parent_id: string | null;
  importance: number;
  pinned: boolean;
  is_system: boolean;
  system_key: string | null;
  metadata: Record<string, unknown>;
  session_id: string | null;
  score: number | null;
  last_accessed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  items: MemoryEntry[];
  total: number;
}

export interface MemoryStats {
  total_memories: number;
  categories: Record<string, number>;
}

export interface Trigger {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  config: Record<string, unknown>;
  action_type: string;
  action_config: Record<string, unknown>;
  last_fired_at: string | null;
  next_fire_at: string | null;
  fire_count: number;
  error_count: number;
  created_at: string;
}

export interface TriggerLog {
  id: string;
  trigger_id: string;
  fired_at: string;
  status: string;
  duration_ms: number | null;
  input_payload: Record<string, unknown> | null;
  output_summary: string | null;
  error_message: string | null;
}

export interface FireTriggerResponse {
  log: TriggerLog;
  resolved_session_id: string | null;
  route_mode: string | null;
  used_fallback: boolean | null;
}

export interface TriggerListResponse {
  items: Trigger[];
  total: number;
}

export interface TriggerLogListResponse {
  items: TriggerLog[];
  total: number;
}

export interface ToolSummary {
  name: string;
  description: string;
  risk_level: 'low' | 'medium' | 'high' | string;
  enabled: boolean;
}

export interface ToolListResponse {
  items: ToolSummary[];
}

export interface ToolDetail extends ToolSummary {
  parameters_schema: Record<string, unknown>;
}

export interface ToolExecutionResponse {
  result: Record<string, unknown>;
  duration_ms: number;
}

export interface GitAccount {
  id: string;
  name: string;
  host: string;
  scope_pattern: string;
  author_name: string;
  author_email: string;
  has_read_token: boolean;
  has_write_token: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface GitAccountListResponse {
  items: GitAccount[];
  total: number;
}

export interface ApprovalRecord {
  provider: string;
  approval_id: string;
  status: string;
  pending: boolean;
  label: string;
  session_id: string | null;
  match_key: string | null;
  command: string | null;
  action: string | null;
  description: string | null;
  can_resolve: boolean;
  decision_note: string | null;
  created_at: string | null;
  updated_at: string | null;
  expires_at: string | null;
  metadata: Record<string, unknown>;
}

export interface ApprovalListResponse {
  items: ApprovalRecord[];
  total: number;
}

export interface RuntimeLiveView {
  enabled: boolean;
  available: boolean;
  mode?: string;
  url: string | null;
  ws_url: string | null;
  display: string | null;
  geometry: string | null;
  reason: string | null;
  provider: {
    id: string;
    label: string;
    status: string | null;
    summary: string | null;
    items: Array<{
      key: string;
      label: string;
      value: string;
    }>;
  };
}

export interface RuntimeActionResponse {
  ok: boolean;
  action: string;
  session_id: string;
  detail: string | null;
  result: Record<string, unknown>;
}

export interface RuntimeStatusCheck {
  id: string;
  label: string;
  status: 'pass' | 'fail' | 'warn' | 'skip';
  detail: string | null;
  hint: string | null;
  required: boolean;
  duration_ms: number | null;
}

export interface RuntimeStatusResponse {
  status: 'ready' | 'degraded' | 'not_configured' | 'unreachable' | 'failed';
  summary: string;
  checked_at: string;
  os: 'linux' | 'darwin' | 'unsupported' | 'unknown';
  sandbox: 'bubblewrap' | 'seatbelt' | 'unavailable' | 'unknown';
  runtime: {
    name?: string | null;
    provider?: 'ssh' | 'lima' | 'docker' | null;
    host: string | null;
    port: number | null;
    username: string | null;
    workspaces_dir: string | null;
  };
  checks: RuntimeStatusCheck[];
  capabilities: Record<string, string>;
}

export type RuntimeProvider = 'ssh' | 'lima' | 'docker';
export type RuntimeStatus = 'unknown' | 'creating' | 'stopped' | 'running' | 'ready' | 'error' | 'deleted';

export interface Runtime {
  id: string;
  name: string;
  provider: RuntimeProvider;
  status: RuntimeStatus;
  profile: string | null;
  host: string | null;
  port: number | null;
  username: string | null;
  workspaces_dir: string | null;
  auth_type: 'private_key' | 'password' | null;
  provider_config: {
    desktop: string;
    cpus: number | null;
    memory: string | null;
    disk: string | null;
  };
  provider_state: {
    ssh_config?: string | null;
    lima_name?: string | null;
    container_name?: string | null;
    workspace_volume?: string | null;
    desktop?: string | null;
  } & Record<string, unknown>;
  last_job_id: string | null;
  last_job_status: 'queued' | 'running' | 'succeeded' | 'failed' | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface RuntimeTestResponse {
  ok: boolean;
  detail: string;
  resolved_home: string | null;
  resolved_workspaces_dir: string | null;
}

export interface RuntimeProviderCapability {
  provider: RuntimeProvider;
  available: boolean;
  label: string;
  detail: string;
  missing: string[];
}

export interface RuntimeCapabilitiesResponse {
  providers: RuntimeProviderCapability[];
}

export interface RuntimeJob {
  id: string;
  runtime_id: string | null;
  provider: RuntimeProvider;
  action: 'create' | 'start' | 'stop' | 'delete' | 'rebuild';
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  events: Array<{ timestamp: string; level: 'info' | 'error'; message: string }>;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface RuntimeLifecycleResponse {
  runtime: Runtime;
  job: RuntimeJob;
}

export interface ConfigResponse {
  app_name: string;
  app_env: string;
  jwt_algorithm: string;
  access_token_ttl_seconds: number;
  refresh_token_ttl_seconds: number;
  context_token_budget: number;
  jwt_secret_key: string;
}

export interface AuditLog {
  id: string;
  timestamp: string;
  user_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  status_code: number | null;
  ip_address: string | null;
  request_id: string | null;
}

export interface AuditLogListResponse {
  items: AuditLog[];
  total: number;
}

export type WsConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

export interface WsEvent {
  type: string;
  [key: string]: unknown;
}

export interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
  detail?: string;
}

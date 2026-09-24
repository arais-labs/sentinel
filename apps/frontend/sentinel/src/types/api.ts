export type WorkspaceDesktop = "none" | "xfce" | "weston" | "lxqt" | "gnome" | "plasma";
export type WorkspaceBrowser = 'chromium' | 'firefox' | 'chrome';
export interface Workspace { browser?: WorkspaceBrowser; desktop?: WorkspaceDesktop; revision?: number | null; distribution?: "alpine" | "ubuntu" | "debian"; id: string; name: string; machine_id: string; directory: string; development_tools?: string[]; recovery_available?: boolean; recovery_backup?: string | null; container_state?: 'recovering' | 'checking' | 'preparing' | 'running' | 'stopped' | 'stopping' | 'failed' | 'unavailable'; container_error?: string | null; container_message?: string | null; resources?: { cpus: number; memory_gib: number; disk_gib: number } | null; }

export interface Session {
  workspace_id?: string | null;
  id: string;
  agent_id: string | null;
  parent_session_id?: string | null;
  title: string | null;
  initial_prompt?: string | null;
  latest_system_prompt?: string | null;
  started_at: string;
  is_running: boolean;
  has_unread?: boolean;
  awaiting_input?: boolean;
  pending_form_id?: string | null;
  completion_id?: string | null;
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
  refs?: string[];
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
  last_request_usage: {
    model: string;
    provider: string;
    service_tier: string;
    usage: {
      input_tokens?: number;
      output_tokens?: number;
      input_tokens_details?: { cached_tokens?: number; cache_write_tokens?: number };
      output_tokens_details?: { reasoning_tokens?: number };
    };
    price: { usd: string; rates_as_of: string } | null;
    price_kind: 'api_equivalent' | 'api_list_price';
  } | null;
  session_id: string;
  context_token_budget: number;
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
  provider_options?: Array<{ provider_id: string; model: string; reasoning_levels: string[]; supports_fast_mode?: boolean; reasoning_effort?: string; context_token_budget?: number | null }>;
  context_window_tokens?: number | null;
  context_token_budget?: number | null;
  output_reserve_tokens?: number | null;
  label: string;
  description: string;
  tier: 'fast' | 'normal' | 'hard';
  primary_provider_id?: 'anthropic' | 'openai' | 'openai-codex' | 'gemini' | 'ollama';
  primary_model_id?: string;
  fallback_providers?: Array<{
    provider_id: 'anthropic' | 'openai' | 'openai-codex' | 'gemini' | 'ollama';
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

export interface AgentUsage {
  requests: number;
  unreported_requests: number;
  input_tokens: number;
  output_tokens: number;
  history_incomplete: boolean;
  costs: Record<string, { usd: string; priced_requests: number; unpriced_requests: number }>;
}

export interface SubAgentTask {
  id: string;
  session_id: string;
  name: string;
  scope: string | null;
  status: string;
  allowed_tools: string[];
  turns_used: number;
  tokens_used: number;
  model?: string | null;
  usage?: AgentUsage;
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
  has_token: boolean;
  github_login: string | null;
  verified_at: string | null;
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
  state: string;
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
    provider?: MachineProvider | null;
    host: string | null;
    port: number | null;
    username: string | null;
  };
  checks: RuntimeStatusCheck[];
  capabilities: Record<string, string>;
}

export type MachineProvider = 'ssh' | 'local';
export type MachineStatus = 'unknown' | 'creating' | 'stopped' | 'running' | 'ready' | 'error' | 'deleted';

export interface Machine {
  id: string;
  name: string;
  provider: MachineProvider;
  status: MachineStatus;
  profile: string | null;
  host: string | null;
  port: number | null;
  username: string | null;
  auth_type: 'private_key' | 'password' | null;
  provider_config: Record<string, never>;
  provider_state: Record<string, unknown>;
  status_detail: string | null;
  last_job_id: string | null;
  last_job_status: 'queued' | 'running' | 'succeeded' | 'failed' | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MachineTestResponse {
  ok: boolean;
  detail: string;
  resolved_home: string | null;
}

export interface MachineProviderCapability {
  provider: MachineProvider;
  available: boolean;
  label: string;
  detail: string;
  missing: string[];
  /** Whether this provider has real start/stop/rebuild actions (ssh + local don't). */
  has_lifecycle: boolean;
}

export interface MachineCapabilitiesResponse {
  providers: MachineProviderCapability[];
}

export interface MachineJob {
  id: string;
  machine_id: string | null;
  provider: MachineProvider;
  action: 'create' | 'start' | 'stop' | 'delete' | 'rebuild';
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  events: Array<{ timestamp: string; level: 'info' | 'error'; message: string }>;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface MachineLifecycleResponse {
  machine: Machine;
  job: MachineJob;
}

export interface AuditLog {
  id: string;
  timestamp: string;
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

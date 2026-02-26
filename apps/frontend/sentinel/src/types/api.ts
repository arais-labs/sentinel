export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Session {
  id: string;
  user_id: string;
  agent_id: string | null;
  parent_session_id?: string | null;
  title: string | null;
  initial_prompt?: string | null;
  latest_system_prompt?: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
}

export interface SessionListResponse {
  items: Session[];
  total: number;
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
  id: string;
  label: string;
  description: string;
  tier?: string;
  primary_provider?: string;
  primary_model?: string;
  fallback_provider?: string;
  fallback_model?: string;
  thinking_budget?: number;
  reasoning_effort?: string;
  hidden?: boolean;
}

export interface ModelsResponse {
  models: ModelOption[];
  default: string;
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

export interface PlaywrightLiveView {
  enabled: boolean;
  available: boolean;
  mode?: string;
  url: string | null;
  reason: string | null;
}

export interface ConfigResponse {
  app_name: string;
  app_env: string;
  estop_active: boolean;
  jwt_algorithm: string;
  access_token_ttl_seconds: number;
  refresh_token_ttl_seconds: number;
  araios_url: string;
  jwt_secret_key: string;
  dev_token: string;
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

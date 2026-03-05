export type ToolPayloadKind = 'input' | 'output';

export interface ToolFieldPreview {
  key: string;
  text: string;
  truncated: boolean;
  redacted: boolean;
}

const SENSITIVE_KEY_PATTERN = /(token|secret|password|passphrase|api[_-]?key|authorization|cookie|credential|private[_-]?key)/i;
const SENSITIVE_VALUE_PATTERN = /(sk-[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._-]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})/i;

const GENERIC_INPUT_PRIORITY = [
  'path',
  'method',
  'url',
  'endpoint',
  'command',
  'query',
  'params',
  'id',
  'name',
  'title',
  'channel',
  'body',
  'payload',
];

const GENERIC_OUTPUT_PRIORITY = [
  'ok',
  'success',
  'status',
  'status_code',
  'error',
  'message',
  'id',
  'count',
  'result',
  'url',
  'share_url',
  'body',
];

const TOOL_CRITICAL_FIELDS: Record<string, { input: readonly string[]; output: readonly string[] }> = {
  araios_api: {
    input: ['path', 'method', 'body', 'timeout_seconds'],
    output: ['status_code', 'error', 'message', 'body'],
  },
  topolix_diagram: {
    input: ['title', 'summary', 'dsl'],
    output: ['share_url', 'url', 'error'],
  },
  git_exec: {
    input: ['command', 'workdir'],
    output: ['status', 'exit_code', 'stdout', 'stderr'],
  },
  runtime_exec: {
    input: ['command', 'cwd', 'timeout_seconds', 'detached'],
    output: ['stdout', 'ok', 'returncode', 'timed_out', 'stderr'],
  },
  runtime_job_logs: {
    input: ['job_id', 'limit', 'offset'],
    output: ['stdout', 'stderr', 'status', 'returncode'],
  },
  runtime_job_status: {
    input: ['job_id'],
    output: ['status', 'returncode', 'started_at', 'finished_at'],
  },
  runtime_jobs_list: {
    input: ['limit', 'offset'],
    output: ['count', 'items'],
  },
  runtime_job_stop: {
    input: ['job_id'],
    output: ['ok', 'status', 'message'],
  },
  trigger_create: {
    input: ['name', 'protocol', 'event', 'filter'],
    output: ['id', 'status', 'error'],
  },
  trigger_update: {
    input: ['trigger_id', 'name', 'protocol', 'event'],
    output: ['id', 'status', 'error'],
  },
};

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function parsePayloadJson(raw: string): unknown | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

export function previewPayloadValue(value: unknown, maxChars = 180): { text: string; truncated: boolean } {
  let raw: string;
  if (typeof value === 'string') {
    raw = value;
  } else if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    raw = String(value);
  } else {
    try {
      raw = JSON.stringify(value);
    } catch {
      raw = String(value);
    }
  }
  const normalized = raw.replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxChars) {
    return { text: normalized, truncated: false };
  }
  return { text: `${normalized.slice(0, maxChars)}…`, truncated: true };
}

function normalizeToolName(name: string): string {
  return name.trim().toLowerCase();
}

function criticalKeysForTool(toolName: string, kind: ToolPayloadKind): readonly string[] {
  const specific = TOOL_CRITICAL_FIELDS[normalizeToolName(toolName)];
  if (specific) {
    return specific[kind];
  }
  return kind === 'input' ? GENERIC_INPUT_PRIORITY : GENERIC_OUTPUT_PRIORITY;
}

function uniqueOrderedKeys(candidates: readonly string[], available: Set<string>, fallback: readonly string[], maxFields: number): string[] {
  const picked: string[] = [];
  const seen = new Set<string>();
  for (const key of candidates) {
    if (!available.has(key) || seen.has(key)) continue;
    picked.push(key);
    seen.add(key);
    if (picked.length >= maxFields) return picked;
  }
  for (const key of fallback) {
    if (!available.has(key) || seen.has(key)) continue;
    picked.push(key);
    seen.add(key);
    if (picked.length >= maxFields) return picked;
  }
  return picked;
}

function valueLooksSensitive(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  return SENSITIVE_VALUE_PATTERN.test(value);
}

function redactField(key: string, value: unknown): { text: string; truncated: boolean; redacted: boolean } {
  if (SENSITIVE_KEY_PATTERN.test(key) || valueLooksSensitive(value)) {
    return { text: '[redacted]', truncated: false, redacted: true };
  }
  const preview = previewPayloadValue(value);
  return { text: preview.text, truncated: preview.truncated, redacted: false };
}

export function topLevelPayloadFieldCount(raw: string): number {
  const parsed = parsePayloadJson(raw);
  if (isObjectRecord(parsed)) {
    return Object.keys(parsed).length;
  }
  if (parsed === null) return 0;
  return 1;
}

export function extractCriticalToolFields({
  toolName,
  raw,
  kind,
  maxFields = 3,
}: {
  toolName: string;
  raw: string;
  kind: ToolPayloadKind;
  maxFields?: number;
}): ToolFieldPreview[] {
  if (!raw.trim()) return [];
  const parsed = parsePayloadJson(raw);
  if (isObjectRecord(parsed)) {
    const entries = Object.entries(parsed);
    if (!entries.length) return [];
    const available = new Set(entries.map(([key]) => key));
    const fallback = entries.map(([key]) => key);
    const ordered = uniqueOrderedKeys(
      criticalKeysForTool(toolName, kind),
      available,
      fallback,
      Math.max(1, maxFields),
    );
    return ordered.map((key) => {
      const value = parsed[key];
      const preview = redactField(key, value);
      return {
        key,
        text: preview.text,
        truncated: preview.truncated,
        redacted: preview.redacted,
      };
    });
  }

  if (parsed !== null) {
    const preview = redactField(kind === 'input' ? 'input' : 'output', parsed);
    return [{
      key: kind === 'input' ? 'input' : 'output',
      text: preview.text,
      truncated: preview.truncated,
      redacted: preview.redacted,
    }];
  }

  const fallback = previewPayloadValue(raw, 180);
  return [{
    key: kind === 'input' ? 'input' : 'output',
    text: fallback.text,
    truncated: fallback.truncated,
    redacted: false,
  }];
}

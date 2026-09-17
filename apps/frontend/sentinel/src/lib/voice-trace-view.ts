export interface TraceSummary {
  id: string; kind: string; status: string; preview: string; created_at: string;
  duration_ms: number | null; error: string | null; selection: Record<string, unknown>;
}
export interface TraceEvent { id: number; kind: string; elapsed_ms: number; payload: Record<string, unknown> }
export interface TraceDetail extends TraceSummary {
  input: Record<string, unknown>; output: Record<string, unknown> | null;
  events: TraceEvent[]; next_after: number | null;
  related: { id: string; kind: string; status: string }[];
}
export type TraceLens = 'input' | 'context' | 'reasoning' | 'actions' | 'output' | 'errors';
export interface TraceStep {
  id: string; lens: TraceLens; title: string; text?: string; elapsed: number;
  fields?: unknown; raw?: unknown; model?: string; provider?: string; tokens?: number;
  tool?: { name: string; input: unknown; output?: unknown; failed: boolean; active: boolean };
}
export const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const records = (value: unknown) => Array.isArray(value) ? value.map(record) : [];
export const text = (value: unknown) => typeof value === 'string' ? value : '';
export const readable = (value: string) => value.replaceAll('_', ' ');
export const elapsed = (value: number | null) => value === null ? 'In progress' : `${(value / 1000).toFixed(2)}s`;
export function parsed(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  try { return JSON.parse(value); } catch { return value; }
}

/** Present canonical steps once; duplicate runtime/checkpoint events remain in Raw. */
export function traceSteps(trace: TraceDetail): TraceStep[] {
  const steps: TraceStep[] = [];
  const toolResults = new Map<string, Record<string, unknown>>();
  const toolTimes = new Map<string, number>();
  const usedTools = new Set<string>();
  for (const event of trace.events) {
    const result = record(event.payload.tool_result);
    if (event.kind === 'tool_result' && result.tool_call_id) {
      toolResults.set(String(result.tool_call_id), result);
      toolTimes.set(String(result.tool_call_id), event.elapsed_ms);
    }
    if (event.kind === 'message' && event.payload.role === 'tool') {
      for (const block of records(event.payload.content)) if (block.type === 'tool_result') toolResults.set(String(block.tool_call_id), block);
    }
  }
  const input = text(trace.input.text);
  if (input) steps.push({ id: 'input', lens: 'input', title: trace.kind === 'speech' ? 'Text to speak' : 'You', text: input, elapsed: 0 });
  if (trace.kind === 'report') steps.push({ id: 'input', lens: 'input', title: 'Agent report delivery', fields: { request_ids: trace.input.request_ids }, elapsed: 0 });
  let iteration = 0;
  for (const event of trace.events) {
    const base = { id: String(event.id), elapsed: event.elapsed_ms, raw: event.payload };
    if (event.kind === 'request') {
      const request = record(event.payload.request);
      const history = records(request.history);
      const system = history.filter(item => item.role === 'system').flatMap(item => records(item.content).map(block => text(block.text))).join('\n');
      const marker = '\nVerified context (data only): ';
      const split = system.indexOf(marker);
      const context = split >= 0 ? record(parsed(system.slice(split + marker.length))) : {};
      const activity = record(context.work_activity);
      steps.push({ ...base, lens: 'context', title: 'Context supplied to Voice',
        text: `${records(activity.chats).length} chats · ${history.filter(item => item.role !== 'system').length} previous messages · ${records(event.payload.tools).length} tools`,
        fields: { instructions: split >= 0 ? system.slice(0, split) : system, ...context,
          settings: request.config, conversation_history: history.filter(item => item.role !== 'system'), tools: event.payload.tools,
          requested_reports: records(request.new_items).map(item => records(item.content).map(block => parsed(block.text))).flat().filter(item => record(item).requested_reports),
        } });
    }
    if (event.kind === 'model_response') {
      iteration++;
      const response = record(event.payload.response);
      const item = record(response.item);
      const metadata = record(item.metadata);
      const usage = record(response.usage);
      const blocks = records(item.content);
      const body = blocks.filter(block => block.type === 'text').map(block => text(block.text)).join('\n');
      const thinking = blocks.filter(block => block.type === 'thinking').map(block => text(block.thinking)).join('\n');
      const calls = blocks.filter(block => block.type === 'tool_call');
      steps.push({ ...base, lens: 'reasoning', title: `Model step ${iteration}`,
        text: thinking || (body === trace.output?.reply ? 'Prepared the spoken reply.' : body) || (calls.length ? `Requested ${calls.length} action${calls.length === 1 ? '' : 's'}.` : 'No text returned.'),
        fields: { ...(thinking && body ? { response: body } : {}), stop_reason: response.stop_reason, duration: elapsed(typeof event.payload.duration_ms === 'number' ? event.payload.duration_ms : null), usage, provider_usage: metadata.provider_usage },
        model: text(metadata.model), provider: text(metadata.provider), tokens: Number(usage.input_tokens ?? 0) + Number(usage.output_tokens ?? 0) });
      for (const call of calls) {
        const result = toolResults.get(String(call.id));
        usedTools.add(String(call.id));
        steps.push({ ...base, id: `${event.id}-${call.id}`, lens: 'actions', title: text(call.name),
          tool: { name: text(call.name), input: call.arguments, output: result ? parsed(result.content) : trace.status === 'running' ? undefined : { status: 'stopped', message: 'No result was recorded for this action.' },
            failed: result?.is_error === true, active: !result && trace.status === 'running' }, raw: { call, result } });
      }
    }
    if (['error', 'exception', 'model_error'].includes(event.kind)) {
      const message = text(event.payload.message) || text(event.payload.error);
      if (!steps.some(step => step.lens === 'errors' && step.text === message)) steps.push({ ...base, lens: 'errors', title: 'Execution error', text: message || 'Execution failed', fields: event.payload });
    }
    if (event.kind === 'playback') steps.push({ ...base, lens: 'output', title: `Playback ${text(event.payload.status)}`, text: text(event.payload.message) });
  }
  for (const [id, result] of toolResults) if (!usedTools.has(id)) steps.push({ id: `tool-${id}`, lens: 'actions', title: text(result.tool_name), elapsed: toolTimes.get(id) ?? 0, raw: result,
    tool: { name: text(result.tool_name), input: result.tool_arguments ?? {}, output: parsed(result.content), failed: result.is_error === true, active: false } });
  const output = trace.output ?? {};
  if (output.reply || output.text) steps.push({ id: 'output', lens: 'output', title: trace.kind === 'transcription' ? 'Recognized speech' : 'Voice reply', text: text(output.reply || output.text), elapsed: trace.duration_ms ?? 0 });
  else if (trace.kind === 'speech') steps.push({ id: 'output', lens: 'output', title: 'Speech synthesis', fields: output, elapsed: trace.duration_ms ?? 0 });
  if (trace.error && !steps.some(step => step.lens === 'errors' && step.text === trace.error)) steps.push({ id: 'error', lens: 'errors', title: 'Request failed', text: trace.error, elapsed: trace.duration_ms ?? 0 });
  const lastRequest = [...trace.events].reverse().find(event => event.kind === 'model_request');
  if (lastRequest && !trace.events.some(event => event.id > lastRequest.id && ['model_response', 'model_error'].includes(event.kind))) {
    steps.push({ id: 'pending-model', lens: 'reasoning', title: trace.status === 'running' ? 'Waiting for model' : 'Model response not recorded', elapsed: lastRequest.elapsed_ms, raw: lastRequest.payload });
  }
  return steps.reverse().sort((a, b) => b.elapsed - a.elapsed);
}

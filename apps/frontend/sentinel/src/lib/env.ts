const rawSessionDebugPanel = (import.meta.env.VITE_SESSION_DEBUG_PANEL as string | undefined)?.trim().toLowerCase();

export const API_BASE_URL = 'sentinel://app/api/v1';
export const SESSION_DEBUG_PANEL_ENABLED = rawSessionDebugPanel === '1' || rawSessionDebugPanel === 'true' || rawSessionDebugPanel === 'yes';

export const WS_BASE_URL = 'sentinel://app';

export const APP_VERSION = '2.3.2';

export function wsSessionsBaseUrl(instanceName: string): string {
  return `${WS_BASE_URL}/ws/instances/${encodeURIComponent(instanceName)}/sessions`;
}

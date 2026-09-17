import type { MicrophonePermission } from '../../shared/ipc.js';

/** Narrow native bridge: microphone only, never camera or arbitrary settings URLs. */
export function createMicrophoneAccess(host: {
  platform: string;
  appName: string;
  getStatus(): MicrophonePermission['status'];
  ask(): Promise<boolean>;
  openExternal(url: string): Promise<void>;
}) {
  let pending: Promise<MicrophonePermission> | undefined;
  const status = (): MicrophonePermission => ({
    status: ['darwin', 'win32'].includes(host.platform) ? host.getStatus() : 'unknown',
    platform: host.platform,
    appName: host.appName,
    canOpenSettings: ['darwin', 'win32'].includes(host.platform),
  });
  return {
    status,
    async request(): Promise<MicrophonePermission> {
      const current = status();
      if (host.platform !== 'darwin' || current.status !== 'not-determined') return current;
      // Several visible panes must not produce duplicate native requests.
      pending ??= host.ask().then(granted => ({ ...status(), status: granted ? 'granted' as const : 'denied' as const })).finally(() => { pending = undefined; });
      return pending;
    },
    async openSettings(): Promise<void> {
      if (host.platform === 'darwin') await host.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone');
      else if (host.platform === 'win32') await host.openExternal('ms-settings:privacy-microphone');
      else throw new Error('Open your system sound settings to enable microphone access.');
    },
  };
}

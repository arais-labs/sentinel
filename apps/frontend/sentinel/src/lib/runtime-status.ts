export type RuntimeInspection = {
  installed: boolean;
  host_key_changed: boolean;
  installed_version?: string;
  available_version?: string;
  update_phase?: string;
  progress?: string;
};

type RuntimePresentation = {
  label: string;
  tone: 'default' | 'good' | 'warn' | 'danger' | 'info';
  action?: string;
  busy?: boolean;
  description: string;
};

export function runtimePresentation(info?: RuntimeInspection, failed = false): RuntimePresentation {
  if (failed) return { label: 'Unavailable', tone: 'default', action: 'Check runtime', description: 'Could not check the remote installation. Check the SSH connection.' };
  if (!info) return { label: 'Checking', tone: 'default', busy: true, description: 'Checking the installed runtime release…' };
  if (info.host_key_changed) return { label: 'Identity changed', tone: 'danger', action: 'Review identity', description: 'Verify this machine’s SSH identity before changing its runtime.' };
  if (info.progress) return { label: 'Updating', tone: 'info', busy: true, description: info.progress };
  if (info.update_phase && !['complete', 'rolled_back'].includes(info.update_phase)) {
    return { label: 'Needs recovery', tone: 'warn', action: 'Recover runtime', description: 'The previous update did not finish. Workspace files are preserved.' };
  }
  if (!info.installed) return { label: 'Not installed', tone: 'default', action: 'Install runtime', description: 'Required to run workspaces on this machine.' };
  if (!info.available_version || !info.installed_version) return { label: 'Version unknown', tone: 'default', action: 'Check runtime', description: 'The installed release could not be compared with this app.' };
  if (info.installed_version !== info.available_version) return { label: 'Update available', tone: 'info', action: 'Update runtime', description: 'This app includes a different runtime release.' };
  return { label: 'Up to date', tone: 'good', action: 'Verify / repair…', description: 'The recorded version matches this app. Verify the installed files and runtime service separately.' };
}

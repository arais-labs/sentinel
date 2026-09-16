import type { DesktopStatus } from '../../../../desktop/sentinel/src/shared/ipc';

export function shouldShowDesktopPreparation(status?: DesktopStatus): boolean {
  if (!status) return true;
  const installingPayload = Boolean(
    status.payloadProgress && status.payloadProgress.phase !== 'done',
  );
  return installingPayload || (
    !status.ready && Boolean(
      status.preparing ||
      status.operation === 'starting' ||
      status.error ||
      !status.payload.installed
    )
  );
}

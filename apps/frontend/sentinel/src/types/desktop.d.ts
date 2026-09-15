import type { DesktopApi } from '../../../../desktop/sentinel/src/shared/ipc';

declare global {
  interface Window { sentinelDesktop?: DesktopApi; }
}

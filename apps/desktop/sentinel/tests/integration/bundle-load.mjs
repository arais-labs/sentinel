// Run with Electron after npm run build. Load the emitted bundle, not tsc output.
import { app } from 'electron';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const profile = mkdtempSync(path.join(os.tmpdir(), 'sentinel-bundle-load-'));
app.setPath('appData', profile);
app.setPath('userData', profile);
app.setPath('sessionData', profile);

// Test module parsing, linking, and top-level initialization without starting
// the backend, UI, or workspace VMs or reading the user's Sentinel profile.
app.whenReady = () => new Promise(() => {});
const timeout = setTimeout(() => {
  console.error('Electron bundle load timed out');
  app.exit(1);
}, 15000);
let exitCode = 0;
try {
  await import('../../dist/main/main.js');
  console.log('Electron production bundle loaded successfully');
} catch (error) {
  console.error(error);
  exitCode = 1;
} finally {
  clearTimeout(timeout);
  rmSync(profile, { recursive: true, force: true });
  app.exit(exitCode);
}

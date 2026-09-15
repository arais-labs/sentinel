// Run with Electron after npm test compiles .test-dist. No real services or user data.
import { app } from 'electron';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
async function run() {
const profile = await mkdtemp(path.join(tmpdir(), 'sentinel-first-launch-'));
app.setPath('userData', profile);
Object.defineProperty(app, 'isPackaged', {value:true});
let code = 0;
try {
  await app.whenReady();
  const { DesktopManager } = await import('../../.test-dist/main/app/desktopManager.js');
  const manager = new DesktopManager();
  let installed = false;
  let ready = false;
  const states = [];
  manager.getStatus = async () => ({ready, preparing:manager.preparing, error:manager.startupError, payload:{installed}, services:[], development:false});
  manager.onStatus(status => states.push(status));
  manager.startServices = async () => manager.getStatus();
  let finish;
  let attempts = 0;
  manager.autoInstallLatest = async () => {
    attempts++;
    await new Promise(resolve => { finish = resolve; });
    throw new Error('Download interrupted');
  };
  const first = manager.initialize();
  assert.equal(manager.initialize(), first, 'concurrent retry shares startup');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal((await manager.getStatus()).preparing, true, 'no stopped-screen gap during download');
  finish();
  const failed = await first;
  assert.equal(failed.preparing, false);
  assert.equal(failed.error, 'Download interrupted');
  assert.equal(attempts, 1);
  manager.autoInstallLatest = async () => { installed = true; ready = true; return true; };
  const retried = await manager.initialize();
  assert.equal(retried.ready, true);
  assert.equal(retried.error, undefined);
  assert.equal(retried.preparing, false);
  assert.ok(states.some(state => state.preparing && !state.ready));
  console.log('First-launch preparation, concurrent retry, download failure and recovery passed');
} catch (error) { console.error(error); code = 1; }
finally { await rm(profile, {recursive:true,force:true}); app.exit(code); }

}
void run();

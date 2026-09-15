import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile, lstat, readlink } from 'node:fs/promises';
import { readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { backupAndReset, validateBackupFolder } from '../../.test-dist/main/app/backupReset.js';

async function fixture(t) {
  const base = await mkdtemp(path.join(tmpdir(), 'sentinel-backup-test-'));
  t.after(() => rm(base, { recursive: true, force: true }));
  const data = path.join(base, 'data');
  const logs = path.join(base, 'logs');
  const destination = path.join(base, 'backups');
  for (const folder of [data, logs, destination]) await mkdir(folder);
  await writeFile(path.join(data, 'database'), 'conversation');
  await writeFile(path.join(logs, 'app.log'), 'log');
  return { base, data, logs, destination, roots: [{ name: 'app-data', path: data }, { name: 'logs', path: logs }] };
}

test('backs up both roots, verifies contents and removes only app-owned data', async t => {
  const f = await fixture(t);
  const external = path.join(f.base, 'project');
  await mkdir(external);
  await writeFile(path.join(external, 'file'), 'keep');
  await symlink(external, path.join(f.data, 'project'));
  const backup = await backupAndReset(f.destination, f.roots);
  assert.equal(await readFile(path.join(backup, 'app-data/database'), 'utf8'), 'conversation');
  assert.equal(await readFile(path.join(backup, 'logs/app.log'), 'utf8'), 'log');
  assert.equal(await readlink(path.join(backup, 'app-data/project')), external);
  assert.equal(await readFile(path.join(external, 'file'), 'utf8'), 'keep');
  await assert.rejects(lstat(f.data), { code: 'ENOENT' });
  await assert.rejects(lstat(f.logs), { code: 'ENOENT' });
  const manifest = JSON.parse(await readFile(path.join(backup, 'manifest.json'), 'utf8'));
  assert.ok(manifest.verifiedAt);
  assert.match(manifest.roots['app-data'].entries.database, /file:.*:[a-f0-9]{64}$/);
  assert.equal((await lstat(backup)).mode & 0o777, 0o700);
});

test('a corrupt second backup prevents deletion of either original root', async t => {
  const f = await fixture(t);
  let corrupt = false;
  await assert.rejects(backupAndReset(f.destination, f.roots, message => {
    if (message === 'Backing up logs…') corrupt = true;
    if (corrupt && message.startsWith('Verifying files')) {
      const backup = path.join(f.destination, readdirSync(f.destination)[0]);
      writeFileSync(path.join(backup, 'logs/app.log'), 'corrupted');
      corrupt = false;
    }
  }), /verification failed/);
  assert.equal(await readFile(path.join(f.data, 'database'), 'utf8'), 'conversation');
  assert.equal(await readFile(path.join(f.logs, 'app.log'), 'utf8'), 'log');
});

test('rejects destinations inside data, including through a symlink', async t => {
  const f = await fixture(t);
  const alias = path.join(f.base, 'alias');
  await symlink(f.data, alias);
  await assert.rejects(validateBackupFolder(alias, f.roots), /outside Sentinel/);
  assert.equal((await readdir(f.destination)).length, 0);
});

test('nested logs are copied once and missing logs do not block reset', async t => {
  const f = await fixture(t);
  await mkdir(path.join(f.data, 'nested'));
  const backup = await backupAndReset(f.destination, [f.roots[0], { name: 'logs', path: path.join(f.data, 'nested') }, { name: 'missing', path: path.join(f.base, 'missing') }]);
  assert.deepEqual((await readdir(backup)).sort(), ['app-data', 'manifest.json', 'reset-complete.txt']);
});

test('refuses a symlink as an application data root', async t => {
  const f = await fixture(t);
  const alias = path.join(f.base, 'alias');
  await symlink(f.data, alias);
  await assert.rejects(backupAndReset(f.destination, [{ name: 'app-data', path: alias }]), /real data directory/);
  assert.equal(await readFile(path.join(f.data, 'database'), 'utf8'), 'conversation');
});

const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { readdirSync, openSync, readSync, closeSync } = require('node:fs');

// --deep verifies nested bundles, but native tools in Resources also need
// explicit verification. Identify code by Mach-O headers, not filename suffixes.
function verifyNativeResources(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) verifyNativeResources(file);
    if (!entry.isFile()) continue;
    const fd = openSync(file, 'r');
    const magic = Buffer.alloc(4);
    try { readSync(fd, magic, 0, 4, 0); } finally { closeSync(fd); }
    if (['feedface', 'cefaedfe', 'feedfacf', 'cffaedfe', 'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca'].includes(magic.toString('hex'))) {
      execFileSync('codesign', ['--verify', '--strict', file], { stdio: 'inherit' });
    }
  }
}

module.exports = async function verifyAppSignature(context) {
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  const resources = path.join(app, 'Contents/Resources');
  const start = Date.now();
  execFileSync(path.join(resources, 'runtime-seed/python/bin/python3'), ['-c',
    'import sys; assert sys.dont_write_bytecode; import asyncio, ssl, sqlite3, ctypes, multiprocessing, http.client, email.message',
  ], { env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: 'inherit', timeout: 10000 });
  verifyNativeResources(resources);
  execFileSync('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], { stdio: 'inherit' });
  console.log(`Runtime execution and signature verification: ${Date.now() - start}ms`);
};

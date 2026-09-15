const { execFileSync } = require('node:child_process');
const path = require('node:path');
module.exports = async function verifyAppSignature(context) {
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  execFileSync('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], { stdio: 'inherit' });
};

import type { SetupStep } from './workspaceTools.js';

// Official ARM64 releases, checked 2026-09-14. Checksums come from upstream release metadata.
export const binaryReleases = {
  "node": {
    "version": "v24.21.0",
    "url": "https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-arm64.tar.xz",
    "checksum": "6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2",
    "check": "test \"$(node --version 2>/dev/null || true)\" = v24.21.0",
    "install": "tar -xJf \"$work/archive\" -C /usr/local --strip-components=1"
  },
  "bun": {
    "version": "1.4.2",
    "url": "https://github.com/oven-sh/bun/releases/download/bun-v1.4.2/bun-linux-aarch64.zip",
    "checksum": "54328bbc2d9c8e0c9f892c544d66c57a83b84139e34909e5ee81758f1ac8fda7",
    "check": "test \"$(bun --version 2>/dev/null || true)\" = 1.4.2",
    "install": "unzip -q \"$work/archive\" -d \"$work\"\ninstall -m 755 \"$work/bun-linux-aarch64/bun\" /usr/local/bin/bun\nln -sf bun /usr/local/bin/bunx"
  },
  "uv": {
    "version": "0.12.13",
    "url": "https://github.com/astral-sh/uv/releases/download/0.12.13/uv-aarch64-unknown-linux-gnu.tar.gz",
    "checksum": "2eaa5d94f5db7b3a1a092156b9420459e42ab0217d917fe74a876309cef9b5e9",
    "check": "uv --version 2>/dev/null | grep -F \"uv 0.12.13\" >/dev/null",
    "install": "tar -xzf \"$work/archive\" -C \"$work\"\ninstall -m 755 \"$work/uv-aarch64-unknown-linux-gnu/uv\" \"$work/uv-aarch64-unknown-linux-gnu/uvx\" /usr/local/bin/"
  },
  "deno": {
    "version": "2.9.6",
    "url": "https://github.com/denoland/deno/releases/download/v2.9.6/deno-aarch64-unknown-linux-gnu.zip",
    "checksum": "9a46afc6c392c7cd2ff71a31558935545b46408d0e87f7a86908c712721c046e",
    "check": "deno --version 2>/dev/null | grep -F \"deno 2.9.6\" >/dev/null",
    "install": "unzip -q \"$work/archive\" -d \"$work\"\ninstall -m 755 \"$work/deno\" /usr/local/bin/deno"
  },
  "kubectl": {
    "version": "v1.36.4",
    "url": "https://dl.k8s.io/release/v1.36.4/bin/linux/arm64/kubectl",
    "checksum": "0ecf44450ee6063bf19dd166a103ee6df4a9034455c2abce626e6eea657d73fb",
    "check": "kubectl version --client -o json 2>/dev/null | grep -F v1.36.4 >/dev/null",
    "install": "install -m 755 \"$work/archive\" /usr/local/bin/kubectl"
  },
  "helm": {
    "version": "v4.3.0",
    "url": "https://get.helm.sh/helm-v4.3.0-linux-arm64.tar.gz",
    "checksum": "31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08",
    "check": "helm version --short 2>/dev/null | grep -F v4.3.0 >/dev/null",
    "install": "tar -xzf \"$work/archive\" -C \"$work\"\ninstall -m 755 \"$work/linux-arm64/helm\" /usr/local/bin/helm"
  },
  "buildx": {
    "version": "v0.37.1",
    "url": "https://github.com/docker/buildx/releases/download/v0.37.1/buildx-v0.37.1.linux-arm64",
    "checksum": "e5cc9fe3bbff5cbc91230981f7860e06076110730a2db997082652199042a1f2",
    "check": "docker buildx version 2>/dev/null | grep -F v0.37.1 >/dev/null",
    "install": "mkdir -p /usr/local/lib/docker/cli-plugins\ninstall -m 755 \"$work/archive\" /usr/local/lib/docker/cli-plugins/docker-buildx"
  },
  "dotnet": {
    "version": "10.0.401",
    "url": "https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.401/dotnet-sdk-10.0.401-linux-arm64.tar.gz",
    "checksum": "58ace73ced6b4360754689a686bdfb8a317f4da6cb8bb416dbc7d0ba9f47e43e3c09f5eb1f1a1cfaacbd10df9558da4882bf2a5e195d6ab56a02c1f9f76102ed",
    "check": "dotnet --list-sdks 2>/dev/null | grep -F \"10.0.401 [\" >/dev/null",
    "install": "mkdir -p /usr/share/dotnet\ntar -xzf \"$work/archive\" -C /usr/share/dotnet\nln -sf /usr/share/dotnet/dotnet /usr/local/bin/dotnet"
  }
} as const;

type Release = (typeof binaryReleases)[keyof typeof binaryReleases];
function installBinary(release: Release): string {
  return `
${release.check} && exit 0
[ "$(uname -m)" = aarch64 ] || { echo 'Unsupported workspace architecture' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 3 --connect-timeout 15 --max-time 600 '${release.url}' -o "$work/archive"
printf '%s  %s\\n' '${release.checksum}' "$work/archive" | ${release.checksum.length === 128 ? 'sha512sum' : 'sha256sum'} -c -
${release.install}
${release.check}
`;
}

// Ubuntu and Debian share these glibc recipes and the existing cluster lifecycle.
export function binaryToolSteps(tools: string[]): SetupStep[] {
  const steps: SetupStep[] = [];
  const add = (message: string, script: string, timeout = 660) => steps.push({ message, arguments: ['sh', '-ec', script], timeout });
  const node = tools.some(tool => ['node', 'pnpm', 'yarn', 'typescript', 'chromium', 'desktop'].includes(tool));
  if (node) add('Installing Node.js…', installBinary(binaryReleases.node));
  for (const id of ['bun', 'uv', 'deno', 'dotnet'] as const) {
    if (tools.includes(id)) add(`Installing ${id === 'dotnet' ? '.NET' : id}…`, installBinary(binaryReleases[id]));
  }
  if (tools.some(tool => ['kubectl', 'kind', 'k3s'].includes(tool))) add('Installing kubectl…', installBinary(binaryReleases.kubectl));
  if (tools.includes('helm')) add('Installing Helm…', installBinary(binaryReleases.helm));
  if (tools.includes('docker-builder')) add('Installing Docker Buildx…', installBinary(binaryReleases.buildx));
  if (tools.includes('pnpm')) add('Installing pnpm…', `
if pnpm --version 2>/dev/null | grep -Fx '12.4.1' >/dev/null; then exit 0; fi
npm install --global --prefix /usr/local --no-audit --no-fund 'pnpm@12.4.1'
pnpm --version
`, 360);
  if (tools.includes('typescript')) add('Installing typescript…', `
if tsc --version 2>/dev/null | grep -Fx 'Version 7.0.2' >/dev/null; then exit 0; fi
npm install --global --prefix /usr/local --no-audit --no-fund 'typescript@7.0.2'
tsc --version
`, 360);
  if (tools.includes('yarn')) add('Installing yarn…', `
if yarn --version 2>/dev/null | grep -Fx '1.22.22' >/dev/null; then exit 0; fi
npm install --global --prefix /usr/local --no-audit --no-fund 'yarn@1.22.22'
yarn --version
`, 360);
  if (tools.includes('chromium') || tools.includes('desktop')) add('Installing Chromium…', `
export PLAYWRIGHT_BROWSERS_PATH=/opt/sentinel/browsers
version=$(node -e 'try { console.log(require("/opt/sentinel/browser-driver/node_modules/playwright/package.json").version) } catch {}')
browser=$(node -e 'try { console.log(require("/opt/sentinel/browser-driver/node_modules/playwright").chromium.executablePath()) } catch {}')
if [ "$version" = 1.63.0 ] && [ -x "$browser" ] && chromium --version >/dev/null 2>&1 && grep -q SENTINEL_CHROMIUM_WRAPPER /usr/local/bin/chromium; then exit 0; fi
if [ "$version" != 1.63.0 ]; then
  npm install --prefix /opt/sentinel/browser-driver --no-audit --no-fund playwright@1.63.0
fi
/opt/sentinel/browser-driver/node_modules/.bin/playwright install --with-deps --no-shell chromium
browser=$(node -e 'console.log(require("/opt/sentinel/browser-driver/node_modules/playwright").chromium.executablePath())')
test -x "$browser"
python3 - "$browser" <<'LAUNCHER'
import pathlib, shlex, sys
path = pathlib.Path("/usr/local/bin/chromium")
path.unlink(missing_ok=True)
path.write_text('#!/bin/sh\\n# SENTINEL_CHROMIUM_WRAPPER\\nexec ' + shlex.quote(sys.argv[1]) + ' \${CHROMIUM_USER_FLAGS:-} "$@"\\n')
path.chmod(0o755)
LAUNCHER
chromium --version
`, 900);
  if (tools.includes('desktop')) add('Preparing desktop commands…', 'test -x /usr/bin/Xtigervnc && ln -sf /usr/bin/Xtigervnc /usr/local/bin/Xvnc', 10);
  return steps;
}

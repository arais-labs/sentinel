/** Package names for the ARM64 apt-based workspace images. Alpine keeps its existing recipes. */
export type WorkspaceDistribution = 'alpine' | 'ubuntu' | 'debian';
export const aptToolPackages: Record<string, string[]> = {
  desktop: ['tigervnc-standalone-server', 'xfce4', 'xfce4-terminal', 'xfce4-whiskermenu-plugin',
    'greybird-gtk-theme', 'numix-gtk-theme', 'papirus-icon-theme', 'python3-xlib', 'python3-pil', 'xdotool',
    'dbus', 'dbus-x11', 'x11-utils', 'x11-xserver-utils', 'fonts-dejavu', 'fonts-noto-core',
    'mesa-utils', 'libegl1', 'libgl1-mesa-dri', 'libxshmfence1', 'libdrm2', 'libzstd1',
    'libexpat1', 'libx11-6', 'libx11-xcb1', 'libxcb-keysyms1', 'libxext6', 'libxcb1', 'libxcb-dri3-0', 'libxcb-present0', 'libxcb-sync1', 'libxfixes3'],
  kind: [], k3s: [], kubectl: [], helm: [], 'docker-builder': [],
  bun: ['libstdc++6'], deno: ['libstdc++6'], uv: ['python3'], dotnet: ['libgcc-s1', 'libstdc++6', 'libgssapi-krb5-2', 'zlib1g', 'libssl3t64'],
  pnpm: ['libstdc++6'], yarn: ['libstdc++6'], typescript: ['libstdc++6'], chromium: ['libstdc++6'],
  git: ['git'], node: ['libstdc++6'], python: ['python3', 'python3-pip', 'python3-venv'],
  poetry: ['python3-poetry'], go: ['golang-go'], rust: ['rustc', 'cargo', 'build-essential'],
  php: ['php-cli', 'php-mbstring', 'php-curl', 'php-xml', 'php-mysql', 'php-pgsql'],
  composer: ['composer'], ruby: ['ruby', 'ruby-dev', 'ruby-bundler', 'build-essential'],
  java: ['openjdk-21-jdk-headless'], maven: ['openjdk-21-jdk-headless', 'maven'], gradle: ['gradle'],
  cpp: ['build-essential'], clang: ['clang', 'build-essential'], cmake: ['cmake'], ninja: ['ninja-build'],
  postgres: ['postgresql-client'], mysql: ['mariadb-client'], redis: ['redis-tools'],
};
export function validateDistribution(value: string): asserts value is WorkspaceDistribution {
  if (!['alpine', 'ubuntu', 'debian'].includes(value)) throw new Error('Unknown workspace distribution');
}
export function aptPackages(tools: string[], distribution: WorkspaceDistribution): string[] {
  if (tools.some(tool => !Object.hasOwn(aptToolPackages, tool))) throw new Error(`Selected tools are not supported on ${distribution}`);
  return [...new Set([
    'tar', 'gzip', 'xz-utils', 'unzip', 'bash', 'tmux', 'git', 'curl', 'python3', 'ca-certificates', 'coreutils', 'findutils',
    'util-linux', 'socat', 'gh', 'ripgrep', 'jq', 'docker.io', 'iptables', 'iproute2',
    distribution === 'ubuntu' ? 'docker-compose-v2' : 'docker-compose',
    ...(distribution === 'debian' ? ['docker-cli'] : []),
    ...(tools.includes('dotnet') ? [distribution === 'ubuntu' ? 'libicu74' : 'libicu76'] : []),
    ...tools.flatMap(tool => aptToolPackages[tool]),
  ])].sort();
}
export const aptInstall = `
export DEBIAN_FRONTEND=noninteractive
# Prevent package postinst scripts from starting daemons outside our VM lifecycle.
policy=/usr/sbin/policy-rc.d
owned_policy=0
if [ ! -e "$policy" ]; then
  printf '#!/bin/sh\nexit 101\n' > "$policy"
  chmod 755 "$policy"
  owned_policy=1
fi
trap '[ "$owned_policy" = 0 ] || rm -f "$policy"' EXIT
reinstall=$1; shift
missing=0
for package in "$@"; do
  [ "$(dpkg-query -W -f='\${Status}' "$package" 2>/dev/null || true)" = 'install ok installed' ] || missing=1
done
if [ "$missing" = 1 ] || [ "$reinstall" = 1 ]; then
  apt-get -o Acquire::Retries=3 update
  if [ "$reinstall" = 1 ]; then
    apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends --reinstall "$@"
  else
    apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "$@"
  fi
fi
# PID 1 starts Docker only once the package transaction has completed.
touch /run/sentinel-docker-ready
`;

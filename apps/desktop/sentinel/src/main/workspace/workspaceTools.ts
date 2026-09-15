import { binaryToolSteps } from './workspaceBinaryTools.js';
import { aptPackages, aptInstall, validateDistribution, type WorkspaceDistribution } from './workspaceDistributions.js';
import { clusterSetupSteps } from './workspaceClusters.js';
// Package names target the workspace image's Alpine stable repositories.
export const toolPackages: Record<string, string[]> = {
  "kind": ["kubectl"],
  "k3s": ["kubectl"],
  "kubectl": ["kubectl"],
  "helm": ["helm"],
  "docker-builder": [],
  "desktop": [
    "tigervnc", "xfce4", "xfce4-terminal", "xfce4-whiskermenu-plugin",
    "greybird-themes", "greybird-themes-gtk3", "numix-themes-xfwm4", "papirus-icon-theme",
    "py3-xlib", "py3-pillow", "xdotool",
    "dbus", "dbus-x11", "xdpyinfo", "xrandr", "font-dejavu", "font-noto", "chromium",
    "mesa-utils", "mesa-egl", "mesa-dri-gallium", "libxshmfence", "libdrm", "zstd-libs",
    "expat", "libx11", "libxext", "libxcb"
  ],
  "git": [
    "git"
  ],
  "node": [
    "nodejs",
    "npm"
  ],
  "bun": [
    "unzip",
    "libstdc++"
  ],
  "deno": [
    "deno"
  ],
  "pnpm": [
    "nodejs",
    "npm",
    "pnpm"
  ],
  "yarn": [
    "nodejs",
    "npm",
    "yarn"
  ],
  "typescript": [
    "nodejs",
    "typescript"
  ],
  "python": [
    "python3",
    "py3-pip",
    "py3-virtualenv"
  ],
  "uv": [
    "python3",
    "uv"
  ],
  "poetry": [
    "python3",
    "poetry"
  ],
  "go": [
    "go"
  ],
  "rust": [
    "rust",
    "cargo",
    "build-base"
  ],
  "php": [
    "php85",
    "php85-phar",
    "php85-openssl",
    "php85-mbstring",
    "php85-curl",
    "php85-dom",
    "php85-tokenizer",
    "php85-xml",
    "php85-session",
    "php85-pdo",
    "php85-pdo_mysql",
    "php85-pdo_pgsql"
  ],
  "composer": [
    "composer"
  ],
  "ruby": [
    "ruby",
    "ruby-dev",
    "ruby-bundler",
    "build-base"
  ],
  "java": [
    "openjdk21-jdk"
  ],
  "maven": [
    "openjdk21-jdk",
    "maven"
  ],
  "gradle": [
    "gradle"
  ],
  "dotnet": [
    "dotnet10-sdk"
  ],
  "cpp": [
    "build-base"
  ],
  "clang": [
    "clang21",
    "build-base"
  ],
  "cmake": [
    "cmake"
  ],
  "ninja": [
    "ninja-is-really-ninja"
  ],
  "chromium": [
    "chromium"
  ],
  "postgres": [
    "postgresql18-client"
  ],
  "mysql": [
    "mariadb-client"
  ],
  "redis": [
    "redis"
  ]
};

export const baselinePackages = ["bash", "tmux", "git", "curl", "python3", "ca-certificates", "coreutils", "findutils", "util-linux", "socat", "github-cli", "ripgrep", "jq"];

export interface SetupStep { message: string; arguments: string[]; timeout: number }
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";

const bunInstall = `
[ "$(bun --version 2>/dev/null || true)" = '1.4.2' ] && exit 0
case "$(uname -m)" in
  aarch64) target=aarch64; checksum=71760b6c8ea30623b81a4907cb815d48e2ea266f2e73e751534a44a0607950df ;;
  x86_64) target=x64; checksum=4835eca59d6da70f4674f5642f6e459dcadab773695b2ed9922d131057989742 ;;
  *) echo 'Unsupported Bun architecture' >&2; exit 1 ;;
esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 2 --connect-timeout 15 --max-time 300 \
  "https://github.com/oven-sh/bun/releases/download/bun-v1.4.2/bun-linux-$target-musl.zip" -o "$work/bun.zip"
printf '%s  %s\\n' "$checksum" "$work/bun.zip" | sha256sum -c -
unzip -q "$work/bun.zip" -d "$work"
install -m 755 "$work/bun-linux-$target-musl/bun" /usr/local/bin/bun
ln -sf bun /usr/local/bin/bunx
bun --version
`;

// These containers run in the workspace's own Docker engine. Loopback ports are
// inside that VM, and volumes live on its private disk, never in the shared project.
const serviceDefinitions: Record<string, object> = {
  postgres: {
    image: 'postgres:18-alpine', restart: 'unless-stopped',
    ports: ['127.0.0.1:5432:5432'],
    environment: { POSTGRES_USER: 'sentinel', POSTGRES_DB: 'workspace', POSTGRES_PASSWORD: '${POSTGRES_PASSWORD}' },
    volumes: ['postgres-data:/var/lib/postgresql'],
    healthcheck: { test: ['CMD-SHELL', 'pg_isready -U sentinel -d workspace'], interval: '3s', timeout: '3s', retries: 30 },
  },
  mysql: {
    image: 'mysql:8.4', restart: 'unless-stopped',
    ports: ['127.0.0.1:3306:3306'],
    environment: { MYSQL_USER: 'sentinel', MYSQL_DATABASE: 'workspace', MYSQL_PASSWORD: '${MYSQL_PASSWORD}', MYSQL_ROOT_PASSWORD: '${MYSQL_ROOT_PASSWORD}' },
    volumes: ['mysql-data:/var/lib/mysql'],
    healthcheck: { test: ['CMD-SHELL', 'mysqladmin ping -h 127.0.0.1 --silent'], interval: '3s', timeout: '3s', retries: 30 },
  },
  redis: {
    image: 'redis:8-alpine', restart: 'unless-stopped', ports: ['127.0.0.1:6379:6379'],
    command: ['redis-server', '--appendonly', 'yes'], volumes: ['redis-data:/data'],
    healthcheck: { test: ['CMD', 'redis-cli', 'ping'], interval: '3s', timeout: '3s', retries: 30 },
  },
};

function servicesScript(tools: string[]): string {
  const selected = tools.filter(tool => Object.hasOwn(serviceDefinitions, tool));
  const config = JSON.stringify({
    services: Object.fromEntries(selected.map(id => [id, serviceDefinitions[id]])),
    volumes: Object.fromEntries(selected.map(id => [id + '-data', {}])),
  });
  const instructions = `Workspace services\n\nOnly accessible inside this workspace. Data persists across sessions and restarts.\nCredentials: /etc/sentinel/services/credentials.env (readable only by root).\nPostgreSQL: localhost:5432, database workspace, user sentinel\nMySQL: localhost:3306, database workspace, user sentinel\nRedis: localhost:6379\nManage: docker compose -p sentinel-services --env-file /etc/sentinel/services/credentials.env -f /etc/sentinel/services/compose.json <command>\nOnly selected services are installed. Removing the workspace deletes these services and their data.\n`;
  return `
umask 077
mkdir -p /etc/sentinel/services
cd /etc/sentinel/services
if [ ! -f credentials.env ]; then
  for key in POSTGRES_PASSWORD MYSQL_PASSWORD MYSQL_ROOT_PASSWORD; do
    printf '%s=%s\\n' "$key" "$(head -c 24 /dev/urandom | base64)"
  done > credentials.env.next
  mv credentials.env.next credentials.env
fi
printf '%s\\n' ${quote(config)} > compose.json.next
mv compose.json.next compose.json
printf '%s' ${quote(instructions)} > README.md
docker compose -p sentinel-services --env-file credentials.env -f compose.json up -d --wait --wait-timeout 120
`;
}

export function workspaceSetupSteps(tools: string[], { reinstall = false, distribution = 'alpine' }: { reinstall?: boolean; distribution?: WorkspaceDistribution } = {}): SetupStep[] {
  validateDistribution(distribution);
  if (distribution !== 'alpine') {
    const steps: SetupStep[] = [{ message: reinstall ? 'Reinstalling workspace tools…' : 'Installing your selected tools…', arguments: ['sh', '-ec', aptInstall, 'sentinel', reinstall ? '1' : '0', ...aptPackages(tools, distribution)], timeout: 900 }];
    steps.push({ message: 'Checking your workspace…', arguments: ['sh', '-ec', 'for i in $(seq 1 30); do timeout 2 docker info >/dev/null 2>&1 && exit 0; sleep 1; done; exec timeout 3 docker info'], timeout: 120 });
    steps.push(...binaryToolSteps(tools));
    if (tools.some(tool => Object.hasOwn(serviceDefinitions, tool))) steps.push({ message: 'Preparing your database services…', arguments: ['sh', '-ec', servicesScript(tools)], timeout: 900 });
    steps.push(...clusterSetupSteps(tools));
    return steps;
  }
  if (tools.some(tool => !Object.hasOwn(toolPackages, tool))) throw new Error('Unknown workspace tools');
  const selected = [...new Set([...baselinePackages, ...tools.flatMap(tool => toolPackages[tool])])].sort();
  const steps: SetupStep[] = [{ message: reinstall ? 'Reinstalling workspace tools…' : 'Installing your selected tools…', arguments: [
    'sh', '-ec', reinstall ? 'exec apk add --no-cache --upgrade "$@"' : 'apk info -e "$@" >/dev/null 2>&1 || exec apk add --no-cache "$@"', 'sentinel', ...selected,
  ], timeout: 600 }];
  if (tools.includes('clang')) steps.push({ message: 'Configuring Clang…', arguments: ['sh', '-ec', 'for name in clang clang++; do command -v "$name" >/dev/null || ln -s /usr/lib/llvm21/bin/"$name" /usr/local/bin/"$name"; done'], timeout: 10 });
  if (tools.includes('bun')) steps.push({ message: 'Installing Bun…', arguments: ['sh', '-ec', bunInstall], timeout: 360 });
  steps.push({ message: 'Checking your workspace…', arguments: [
    'sh', '-ec', 'for i in $(seq 1 20); do timeout 2 docker info >/dev/null 2>&1 && exit 0; sleep 1; done; exec timeout 3 docker info',
  ], timeout: 90 });
  if (tools.some(tool => Object.hasOwn(serviceDefinitions, tool))) steps.push({
    message: 'Preparing your database services…', arguments: ['sh', '-ec', servicesScript(tools)], timeout: 900,
  });
  steps.push(...clusterSetupSteps(tools));
  return steps;
}

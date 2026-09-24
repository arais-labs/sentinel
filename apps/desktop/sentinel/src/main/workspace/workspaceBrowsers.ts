import type { WorkspaceDistribution } from './workspaceDistributions.js';
import { aptInstall } from './workspaceDistributions.js';
import type { SetupStep } from './workspaceTools.js';

export type WorkspaceBrowser = 'chromium' | 'firefox' | 'chrome';

export function validateBrowser(browser: string, distribution: WorkspaceDistribution): asserts browser is WorkspaceBrowser {
  if (!['chromium', 'firefox', 'chrome'].includes(browser)) throw new Error('Unknown workspace browser');
  if (browser === 'chrome' && distribution === 'alpine') throw new Error('Google Chrome is supported on Ubuntu and Debian, not Alpine');
}

/** Additive installation: changing the default never deletes packages or profiles. */
export function browserSetupSteps(browser: WorkspaceBrowser, distribution: WorkspaceDistribution): SetupStep[] {
  validateBrowser(browser, distribution);
  const steps: SetupStep[] = [];
  if (browser === 'firefox' && distribution === 'alpine') {
    steps.push({ message: 'Installing Firefox…', arguments: ['sh', '-ec', 'apk info -e firefox >/dev/null 2>&1 || apk add --no-cache firefox'], timeout: 600 });
  } else if (browser === 'firefox') {
    // Ubuntu's firefox package is a Snap transition. Use Mozilla's native DEB
    // repository there; Debian provides its maintained native Firefox ESR.
    const repository = distribution === 'ubuntu' ? `
if [ -f /etc/apt/sources.list.d/mozilla.list ] && command -v firefox >/dev/null && [ -x /usr/lib/firefox/firefox ]; then exit 0; fi
export DEBIAN_FRONTEND=noninteractive
if ! command -v gpg >/dev/null; then
  apt-get -o Acquire::Retries=3 update
  apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends gpg
fi
install -d -m 755 /etc/apt/keyrings
key=$(mktemp)
trap 'rm -f "$key"' EXIT
curl --fail --location --retry 3 --connect-timeout 15 --max-time 60 https://packages.mozilla.org/apt/repo-signing-key.gpg -o "$key"
fingerprint=$(gpg --batch --show-keys --with-colons "$key" | awk -F: '$1 == "fpr" { print $10; exit }')
[ "$fingerprint" = '35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3' ] || { echo 'Mozilla signing key does not match' >&2; exit 1; }
install -m 644 "$key" /etc/apt/keyrings/packages.mozilla.org.asc
rm -f "$key"
trap - EXIT
printf '%s\\n' 'deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main' > /etc/apt/sources.list.d/mozilla.list
printf '%s\\n' 'Package: *' 'Pin: origin packages.mozilla.org' 'Pin-Priority: 1000' > /etc/apt/preferences.d/mozilla
` : '';
    steps.push({ message: 'Installing Firefox…', arguments: ['sh', '-ec', repository + aptInstall, 'sentinel', '0', distribution === 'ubuntu' ? 'firefox' : 'firefox-esr'], timeout: 900 });
  } else if (browser === 'chrome') {
    steps.push({ message: 'Installing Google Chrome…', arguments: ['sh', '-ec', `
if command -v google-chrome-stable >/dev/null; then exit 0; fi
arch=$(dpkg --print-architecture)
case "$arch" in arm64|amd64) ;; *) echo 'Unsupported Chrome architecture' >&2; exit 1 ;; esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 3 --connect-timeout 15 --max-time 600 "https://dl.google.com/linux/direct/google-chrome-stable_current_$arch.deb" -o "$work/chrome.deb"
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=3 update
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends "$work/chrome.deb"
google-chrome-stable --version
`], timeout: 900 });
  }
  const chromium = distribution === 'ubuntu' ? '/snap/bin/chromium' : 'chromium';
  const desktop = browser === 'chrome' ? 'google-chrome-stable' : browser === 'firefox' && distribution === 'debian' ? 'firefox-esr' : browser === 'chromium' ? chromium : browser;
  const automation = browser === 'chrome' ? 'google-chrome-stable' : chromium;
  const name = browser === 'chrome' ? 'Google Chrome' : browser === 'firefox' ? (distribution === 'debian' ? 'Firefox ESR' : 'Firefox') : 'Chromium';
  const icon = browser === 'chrome' ? 'google-chrome' : browser === 'firefox' && distribution === 'debian' ? 'firefox-esr' : browser === 'chromium' && distribution === 'ubuntu' ? '/snap/chromium/current/chromium.png' : browser;
  steps.push({ message: 'Configuring workspace browser…', arguments: ['sh', '-ec', `
command -v ${desktop} >/dev/null
command -v ${automation} >/dev/null
${distribution === 'alpine' ? `# Use Chromium's existing disk-cache backend: its newer GPU-process SQLite
# cache issues musl pwritev2 calls rejected by the stock GPU sandbox.
# The distro launcher sources this for both desktop and automation launches.
install -d -m 755 /etc/chromium
printf '%s\\n' 'CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-features=GpuPersistentCache"' > /etc/chromium/sentinel.conf
chmod 644 /etc/chromium/sentinel.conf
` : ''}
install -d /usr/local/bin /usr/share/applications /etc/xdg /etc/sentinel
printf '#!/bin/sh\\nexec ${desktop} "$@"\\n' > /usr/local/bin/sentinel-browser
printf '#!/bin/sh\\nexec ${automation} "$@"\\n' > /usr/local/bin/sentinel-automation-browser
chmod 755 /usr/local/bin/sentinel-browser /usr/local/bin/sentinel-automation-browser
printf '%s\\n' '[Desktop Entry]' 'Type=Application' 'Name=${name}' 'Exec=sentinel-browser %U' 'Icon=${icon}' 'Terminal=false' 'Categories=Network;WebBrowser;' 'MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;' > /usr/share/applications/sentinel-browser.desktop
python3 - <<'PREFERENCES'
import configparser
import os
import pwd
from pathlib import Path
def defaults(path):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(path)
    if not config.has_section('Default Applications'):
        config.add_section('Default Applications')
    for mime in ('text/html', 'x-scheme-handler/http', 'x-scheme-handler/https'):
        config['Default Applications'][mime] = 'sentinel-browser.desktop'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as stream:
        config.write(stream, space_around_delimiters=False)
defaults(Path('/etc/xdg/mimeapps.list'))
try:
    account = pwd.getpwnam('sentinel')
except KeyError:
    pass  # Headless workspaces use system defaults; no desktop account needed.
else:
    os.setgroups(os.getgrouplist(account.pw_name, account.pw_gid))
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    defaults(Path(account.pw_dir) / '.config/mimeapps.list')
PREFERENCES
printf '%s\\n' '${browser}' > /etc/sentinel/browser-selection
`], timeout: 30 });
  return steps;
}

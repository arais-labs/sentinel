#!/bin/bash
# Install the published stable app; no sudo, quarantine removal, or Gatekeeper changes.
set -euo pipefail

main() {
  if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    echo 'Sentinel requires an Apple Silicon Mac.' >&2
    return 1
  fi
  if (( ${MACOS_VERSION%%.*} < 26 )); then
    echo 'Sentinel requires macOS 26 or newer.' >&2
    return 1
  fi
  if [[ ! -w /Applications ]]; then
    echo 'Applications is not writable. Use the DMG installer in Finder instead.' >&2
    return 1
  fi
  if pgrep -f '^/Applications/Sentinel.app/Contents/MacOS/Sentinel([[:space:]]|$)' >/dev/null; then
    echo 'Quit Sentinel, then run this command again.' >&2
    return 1
  fi
  if [[ -L /Applications/Sentinel.app ]]; then
    echo 'Refusing to replace a symlink at /Applications/Sentinel.app.' >&2
    return 1
  fi

  download=$(mktemp -d "${TMPDIR:-/tmp}/sentinel-install.XXXXXX")
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  local base=https://github.com/arais-labs/sentinel/releases/download
  curl --fail --show-error --location --silent "$base/latest-stable/latest-stable.json" -o "$download/index.json"
  local version commit checksum
  version=$(plutil -extract version raw -o - "$download/index.json")
  commit=$(plutil -extract commit raw -o - "$download/index.json")
  checksum=$(plutil -extract installerSha256 raw -o - "$download/index.json")
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$commit" =~ ^[0-9a-f]{40}$ && "$checksum" =~ ^[0-9a-f]{64}$ ]] || {
    echo 'Invalid release metadata.' >&2
    return 1
  }
  echo "Downloading Sentinel ${version}..."
  # Use the immutable release so a concurrent publication cannot change this download.
  curl --fail --show-error --location "$base/stable-$version-${commit:0:7}/Sentinel-$version-arm64.dmg" -o "$download/Sentinel.dmg"
  printf '%s  %s\n' "$checksum" "$download/Sentinel.dmg" | shasum -a 256 -c -
  hdiutil attach "$download/Sentinel.dmg" -readonly -nobrowse -mountpoint "$download/mounted"
  mounted=true
  codesign --verify --deep --strict "$download/mounted/Sentinel.app"

  # Stage on the destination filesystem before touching an existing installation.
  staging=$(mktemp -d /Applications/.sentinel-install.XXXXXX)
  ditto "$download/mounted/Sentinel.app" "$staging/Sentinel.app"
  codesign --verify --deep --strict "$staging/Sentinel.app"
  if [[ -e /Applications/Sentinel.app ]]; then
    mv /Applications/Sentinel.app "$staging/Previous Sentinel.app"
  fi
  mv "$staging/Sentinel.app" /Applications/Sentinel.app
  echo "Installed Sentinel $version in Applications. Open Sentinel to get started."
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "$staging" ]]; then
    if [[ -e "$staging/Previous Sentinel.app" ]]; then
      if [[ ! -e /Applications/Sentinel.app ]]; then
        mv "$staging/Previous Sentinel.app" /Applications/Sentinel.app || true
      else
        echo "Previous app preserved at: $staging/Previous Sentinel.app"
      fi
    fi
    # Never delete a previous installation, including after a failed rollback.
    if [[ ! -e "$staging/Previous Sentinel.app" ]]; then
      rm -rf -- "$staging"
    fi
  fi
  if [[ "$mounted" == true ]]; then
    hdiutil detach "$download/mounted" || { echo "Installer files retained at: $download"; exit "$status"; }
  fi
  rm -rf -- "$download"
  exit "$status"
}

download='' staging='' mounted=false
MACOS_VERSION=$(sw_vers -productVersion 2>/dev/null || echo 0)
main

#!/bin/sh
# Use distro manifests, ABI checks and dependency generation; compile no targets.
set -eu
if [ "$#" -ne 3 ]; then
  echo 'Usage: package-deb.sh ABSOLUTE_SOURCE ABSOLUTE_BUILD ABSOLUTE_OUTPUT' >&2
  exit 2
fi
for path in "$@"; do
  case "$path" in
    /*) ;;
    *) echo "Package paths must be absolute: $path" >&2; exit 2 ;;
  esac
done
# Match the distro's dh-sequence-pkgkde-symbolshelper substitution handling.
export PATH="/usr/share/pkg-kde-tools/bin:$PATH"
source=$1
build=$2
output=$3
package=libklipper6
original=$(dpkg-parsechangelog -l "$source/debian/changelog" -S Version)
# A warm package operation must not manufacture a new build solely because the
# wall clock advanced. Use the pinned distro changelog as the packaging epoch.
SOURCE_DATE_EPOCH=$(dpkg-parsechangelog -l "$source/debian/changelog" -S Timestamp)
export SOURCE_DATE_EPOCH
case "$original" in
  4:6.6.6-0ubuntu0.1|4:6.3.6-2) ;;
  *) echo "Unqualified distro source version: $original" >&2; exit 2 ;;
esac
architecture=$(dpkg-architecture -qDEB_HOST_ARCH)
multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
version="$original+sentinel1"
upstream=$(dpkg-parsechangelog -l "$source/debian/changelog" -S Version | sed 's/^[0-9]*://; s/-[^-]*$//')
library="libklipper.so.$upstream"
test -f "$source/debian/$package.install"
test -f "$source/debian/$package.symbols"
test -f "$build/bin/$library"
# Reject a library from a different target/ABI before packaging it under the
# distro's owning package. Strict debhelper symbols/dependencies are checked below.
soname=$(readelf -d "$build/bin/$library" | sed -n 's/.*(SONAME).*\[\([^]]*\)\].*/\1/p')
test "$soname" = libklipper.so.6
mkdir -p "$output"
work=$(mktemp -d "$output/package.XXXXXX")
cp -a "$source/debian" "$work/debian"
cd "$work"
{
  printf 'plasma-workspace (%s) UNRELEASED; urgency=medium\n\n' "$version"
  printf '  * Make saved clipboard restoration conditional; preserve explicit selection.\n\n'
  printf ' -- Sentinel Developers <build@sentinel.local>  %s\n\n' \
    "$(LC_ALL=C date -Ru -d "@$SOURCE_DATE_EPOCH")"
  cat "$source/debian/changelog"
} > debian/changelog
mkdir -p "stage/usr/lib/$multiarch"
install -m 755 "$build/bin/$library" "stage/usr/lib/$multiarch/$library"
ln -s "$library" "stage/usr/lib/$multiarch/libklipper.so.6"
dh_install -p"$package" --sourcedir=stage
dh_installdocs -p"$package"
dh_installchangelogs -p"$package"
dh_strip -p"$package" --no-automatic-dbgsym
dh_compress -p"$package"
dh_fixperms -p"$package"
dh_makeshlibs -p"$package" -- -c4
dh_shlibdeps -p"$package"
dh_gencontrol -p"$package"
dh_md5sums -p"$package"
dh_builddeb -p"$package" --destdir="$output"
printf 'Built %s %s %s\n' "$package" "$version" "$architecture"

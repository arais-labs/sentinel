#!/bin/sh
# Distro-prepared source, with the Sentinel patch and regression already applied.
# CMake/Ninja own compilation. Never invoke the workspace-wide all/install target.
set -eu
if [ "$#" -ne 2 ]; then
  echo 'Usage: build-deb.sh ABSOLUTE_SOURCE ABSOLUTE_BUILD' >&2
  exit 2
fi
for path in "$@"; do
  case "$path" in /*) ;; *) echo "Build paths must be absolute: $path" >&2; exit 2 ;; esac
done
source_tree=$1
build_tree=$2
jobs=${SENTINEL_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}
case "$jobs" in ''|*[!0-9]*|0) echo 'Invalid positive job count' >&2; exit 2 ;; esac
test -f "$source_tree/klipper/autotests/startuprestoretest.cpp"
version=$(dpkg-parsechangelog -l"$source_tree/debian/changelog" -SVersion)
case "$version" in
  4:6.6.6-0ubuntu0.1|4:6.3.6-2) ;;
  *) echo "Unqualified distro source version: $version" >&2; exit 2 ;;
esac
# Both pinned distro recipes use these hardening/configuration settings.
# Let debhelper own compiler flags, multiarch paths and CMake build type.
export DEB_BUILD_MAINT_OPTIONS=hardening=+all
set -- -DBUILD_TESTING=ON -DPLASMA_X11_DEFAULT_SESSION=OFF -DWITH_X11=ON
case "$version" in *ubuntu*) set -- "$@" -DUBUNTU_PACKAGEKIT=FALSE ;; esac
if command -v ccache >/dev/null 2>&1; then
  set -- "$@" -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
fi
cd "$source_tree"
dh_auto_configure --buildsystem=cmake+ninja --builddirectory="$build_tree" -- "$@"
cmake --build "$build_tree" --parallel "$jobs" --target klipper klipper-startuprestoretest

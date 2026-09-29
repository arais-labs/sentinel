#!/bin/sh
# Called by native abuild in the prepared source tree. Packaging lives elsewhere.
set -eu
CFLAGS="${CFLAGS:-} -O2 -g1" CXXFLAGS="${CXXFLAGS:-} -O2 -g1" \
    cmake -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib \
    -DINSTALL_SDDM_WAYLAND_SESSION=ON -DBUILD_TESTING=ON \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
cmake --build build --parallel "$JOBS" --target \
    batterycontrol kfontinst kfontinstui klipper klookandfeel kmpris \
    kworkspace notificationmanager taskmanager klipper-startuprestoretest

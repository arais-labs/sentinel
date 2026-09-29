# Appended to the checksum-pinned distro APKBUILD. Keep origin, license,
# dependencies, source checksums and distro patch; emit only the complete native
# libraries subpackage into the final bundle. The staging parent is never shipped.
pkgrel=1
subpackages="$pkgname-libs"

package() {
	# Do not invoke the workspace-wide cmake install target. The signed distro
	# owner APK's complete library file list is checked again after packaging.
	install -d "$pkgdir/usr/lib"
	for library in batterycontrol kfontinst kfontinstui klipper klookandfeel \
		kmpris kworkspace6 notificationmanager taskmanager; do
		install -m755 "build/bin/lib$library.so.$pkgver" "$pkgdir/usr/lib/"
		soname=6
		[ "$library" != notificationmanager ] || soname=1
		ln -s "lib$library.so.$pkgver" "$pkgdir/usr/lib/lib$library.so.$soname"
	done
	# The distro keeps this unversioned provider in the parent, not -libs.
	# Stage it so abuild resolves the same native so: dependency; never ship
	# this incomplete parent in place of the distribution's desktop package.
	install -m755 build/bin/libkrdb.so "$pkgdir/usr/lib/"
	cmake -DLIBRARY_DIR="$pkgdir/usr/lib" -P "$startdir/normalize-alpine.cmake"
}

# Workspaces build entry point for development and CI. Outputs stay in build/; nothing is
# installed on the host. Sources are pinned by SHA-256.

BUILD := build
SRC := $(BUILD)/src
TOOLS := $(BUILD)/tools

E2FS := e2fsprogs-1.47.4
E2FS_URL := https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v1.47.4/$(E2FS).tar.xz
E2FS_SHA256 := fd5bf388cbdbe006a3d3b318d983b2948382440acc85a87f1e7d108653e8db0b

# Headers only: mke2fs links the libarchive that macOS ships (3.7.4), so the headers match it.
ARCHIVE := libarchive-3.7.4
ARCHIVE_URL := https://github.com/libarchive/libarchive/releases/download/v3.7.4/$(ARCHIVE).tar.xz
ARCHIVE_SHA256 := f887755c434a736a609cbd28d87ddbfbe9d6a3bb5b703c22c02f6af80a802735

# Install media for distributions sentineld installs (DESIGN.md §8): wimlib-imagex reads and writes
# Windows images; FatFs, built into sentineld, writes the FAT32 and exFAT partitions of a media disk.
WIMLIB := wimlib-1.14.4
WIMLIB_URL := https://wimlib.net/downloads/$(WIMLIB).tar.gz
WIMLIB_SHA256 := 3633db2b6c8b255eb86d3bf3df3059796bd1f08e50b8c9728c7eb66662e51300
# Windows system disks are written on the host (DESIGN.md §7): NTFS-3G formats the volume and lets
# wimlib apply an image into it (both reaching a partition inside a disk image through the volume-offset
# patch); hivex edits the offline registry and boot store.
NTFS3G := ntfs-3g_ntfsprogs-2022.10.3
NTFS3G_URL := https://download.tuxera.com/opensource/$(NTFS3G).tgz
NTFS3G_SHA256 := f20e36ee68074b845e3629e6bced4706ad053804cbaf062fbae60738f854170c
HIVEX := hivex-1.3.24
HIVEX_URL := https://download.libguestfs.org/hivex/$(HIVEX).tar.gz
HIVEX_SHA256 := a52fa45cecc9a78adb2d28605d68261e4f1fd4514a778a5473013d2ccc8a193c
FATFS := ff16
FATFS_URL := https://elm-chan.org/fsw/ff/arc/$(FATFS).zip
FATFS_SHA256 := 99f7dc1f7e095356e4a9e3dbe29959090d8b948afe2bbc5441e52fdf4b85449e
FATFS_OBJ := $(BUILD)/obj/ff.o $(BUILD)/obj/ffunicode.o

# sentineld-media's QUIC stack (WebTransport, DESIGN.md §6): static, pinned, built into build/deps.
# HTTP/3 is ours (media/server.cpp): WebTransport needs only a sliver of it, and settings no library sends.
OPENSSL := openssl-3.5.9
OPENSSL_URL := https://github.com/openssl/openssl/releases/download/$(OPENSSL)/$(OPENSSL).tar.gz
OPENSSL_SHA256 := 603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
NGTCP2 := ngtcp2-1.25.0
NGTCP2_URL := https://github.com/ngtcp2/ngtcp2/releases/download/v1.25.0/$(NGTCP2).tar.xz
NGTCP2_SHA256 := 2a34d2484ba17847a5d11965704e9dd0fac4c6d8efc75ffe1ec7de66d8c6b6fb
DEPS := $(BUILD)/deps

CXX := clang++
# The Command Line Tools SDK can be newer than the linker; build against the selected toolchain's SDK.
export SDKROOT := $(shell xcrun --sdk macosx --show-sdk-path)
CXXFLAGS := -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror
CORE_SRC := core/util/json.cpp core/util/sha256.cpp core/image/disk.cpp core/image/download.cpp \
	core/image/media.cpp core/image/install.cpp core/loop.cpp core/agent.cpp core/catalog.cpp core/computer.cpp core/media.cpp core/workspaces.cpp core/server.cpp core/update.cpp core/ctl.cpp core/main.cpp \
	core/view/view.mm core/view/transport.cpp
MACOS_SRC := macos/backend/workspace.cpp
MEDIA_SRC := media/main.cpp media/server.cpp core/loop.cpp core/util/json.cpp
AGENT_SRC := guest/agent/agent.cpp core/util/json.cpp

ARCH := arm64
VERSION := $(shell cat ../../VERSION)
# Guest binaries cross-compile on the host with zig's clang, which carries musl, libc++ and mingw for
# both targets (static linux/arm64 and Windows ARM64); the Linux agent links Alpine's libxkbcommon.
ZIG := zig-aarch64-macos-0.17.0
ZIG_URL := https://ziglang.org/download/0.17.0/$(ZIG).tar.xz
ZIG_SHA256 := b607e9b9234790a008116ae5bdb71c6243b84b9fb42a53a9e70fde41c06c536a
XKB_URL := https://dl-cdn.alpinelinux.org/alpine/v3.24/main/aarch64
XKB_DEV := libxkbcommon-dev-1.13.1-r0.apk
XKB_DEV_SHA256 := 0a3542cb9a86e6788e44bd427b88ec92a6f3b1d3634da4724bfc764bbf70cc3c
XKB_STATIC := libxkbcommon-static-1.13.1-r0.apk
XKB_STATIC_SHA256 := e57d72baa8efe2fca9b17c652dec026cde3908a9eb923b794dece5087f829126
GUEST_ROOT := $(SRC)/guest-root
GUEST_CXX := $(SRC)/$(ZIG)/zig c++ -target aarch64-linux-musl -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror \
	-static -s -I$(GUEST_ROOT)/usr/include
WINDOWS_CXX := $(SRC)/$(ZIG)/zig c++ -target aarch64-windows-gnu -std=c++20 -O2 -Wall -Wextra -Werror -static -s

.PHONY: distributions all tools deps share release clean e2e
all: tools $(BUILD)/bin/sentineld $(BUILD)/bin/sentineld-media share distributions $(BUILD)/compile_commands.json
# The share's data: the catalog, the desktop session, the wallpapers.
GUEST_DATA := catalog.json hostname.sh $(patsubst guest/%,%,$(wildcard guest/desktop/*.sh guest/desktop/*.py guest/wallpapers/*.png))
# The distributions sentineld ships (DESIGN.md §8): build/distributions, one folder each, beside the share.
DISTRIBUTIONS := $(patsubst guest/distributions/%,%,$(shell find guest/distributions -type f))
distributions: $(addprefix $(BUILD)/distributions/,$(DISTRIBUTIONS))
	@# A file gone from guest/distributions goes from here too: install keys see only what is.
	@cd $(BUILD)/distributions && find . -type f | sed 's|^\./||' | while read -r f; do \
		case " $(DISTRIBUTIONS) " in *" $$f "*) ;; *) rm -f "$$f" ;; esac; done
	@cd $(BUILD)/distributions && find . -mindepth 1 -type d -empty -delete
$(BUILD)/distributions/%: guest/distributions/%
	@mkdir -p $(@D) && cp -p $< $@
share: $(BUILD)/share/sentineld-agent $(BUILD)/share/windows/sentineld-agent.exe $(BUILD)/share/windows/vulkan/vulkan_virtio.dll \
	$(BUILD)/share/windows/vulkan/vulkan_virtio_x64.dll $(BUILD)/share/windows/msys2/version \
	$(addprefix $(BUILD)/share/,$(GUEST_DATA))
	@# A file gone from guest/ goes from the share too: the guests and install keys see only what is.
	@cd $(BUILD)/share && find distributions desktop wallpapers -type f 2>/dev/null | while read -r f; do \
		case " $(GUEST_DATA) " in *" $$f "*) ;; *) rm -f "$$f" ;; esac; done
	@cd $(BUILD)/share && find distributions desktop wallpapers -type d -empty -delete 2>/dev/null || true
	@# Where each guest mounts its own distribution folder (virtiofs tag "distribution", DESIGN.md §17).
	@mkdir -p $(BUILD)/share/distribution
tools: $(TOOLS)/mke2fs $(TOOLS)/resize2fs $(TOOLS)/wimlib-imagex $(TOOLS)/mkntfs $(TOOLS)/hive

# VMs run in sentinel-workspace (../sentinel-workspace), which holds the hypervisor entitlement.
$(BUILD)/bin/sentineld: $(CORE_SRC) $(MACOS_SRC) $(wildcard core/*.hpp core/*/*.hpp core/help/*.txt) $(FATFS_OBJ) $(DEPS)/lib/libngtcp2_crypto_ossl.a $(TOOLS)/resize2fs
	@mkdir -p $(@D) && $(CXX) $(CXXFLAGS) -fobjc-arc -I$(DEPS)/include -I$(SRC)/$(FATFS)/source -I$(SRC)/$(ARCHIVE)/libarchive -o $@ \
		$(CORE_SRC) $(MACOS_SRC) $(FATFS_OBJ) -lz -lcurl -larchive \
		$(DEPS)/lib/libngtcp2_crypto_ossl.a $(DEPS)/lib/libngtcp2.a $(DEPS)/lib/libssl.a $(DEPS)/lib/libcrypto.a \
		$(addprefix -framework ,IOKit CoreFoundation AppKit AVFoundation CoreMedia)

# The viewer endpoint (DESIGN.md §6): static QUIC stack from build/deps.
$(BUILD)/bin/sentineld-media: $(MEDIA_SRC) $(wildcard media/*.hpp core/*.hpp core/util/*.hpp) $(DEPS)/lib/libngtcp2_crypto_ossl.a
	@mkdir -p $(@D) && $(CXX) $(CXXFLAGS) -I$(DEPS)/include -o $@ $(MEDIA_SRC) \
		$(DEPS)/lib/libngtcp2_crypto_ossl.a $(DEPS)/lib/libngtcp2.a $(DEPS)/lib/libssl.a $(DEPS)/lib/libcrypto.a

# End-to-end tests (DESIGN.md "End-to-end tests"): a private sentineld (state in $$TMPDIR, cache and
# a mirror of the pinned install sources in build/e2e, never the app's) runs every distribution x
# desktop, plus a desktop-less workspace each, as many at once as free memory and disk allow, and
# checks install, exec, the desktop session, video, the cursor a viewer sees, pointer and clicks at
# every edge, stop, restart and layer reuse. Report and evidence: build/e2e/runs/latest/.
#   make e2e [DISTROS=alpine,debian] [DESKTOPS=none,xfce,gnome] [JOBS=2]
# e2e-viewer is the headless viewer it watches the desktop with (WebTransport, like the app's).
$(BUILD)/tests/e2e-viewer: tests/e2e/viewer.cpp $(DEPS)/lib/libngtcp2_crypto_ossl.a
	@mkdir -p $(@D) && $(CXX) $(CXXFLAGS) -I$(DEPS)/include -o $@ tests/e2e/viewer.cpp \
		$(DEPS)/lib/libngtcp2_crypto_ossl.a $(DEPS)/lib/libngtcp2.a $(DEPS)/lib/libssl.a $(DEPS)/lib/libcrypto.a
# e2e-d3d11.exe is the Windows GPU check's probe (tests/e2e/d3d11_probe.c), put in the run's share.
$(BUILD)/tests/e2e-d3d11.exe: tests/e2e/d3d11_probe.c $(SRC)/$(ZIG)/zig
	@mkdir -p $(@D) && $(SRC)/$(ZIG)/zig cc -target aarch64-windows-gnu -O2 -Wall -Wextra -Werror -s -o $@ \
		tests/e2e/d3d11_probe.c -ld3d11 -ldxgi
e2e: all $(BUILD)/tests/e2e-viewer $(BUILD)/tests/e2e-d3d11.exe
	@$(MAKE) -s -C ../sentinel-workspace
	@python3 tests/e2e/e2e.py --distros "$(DISTROS)" --desktops "$(DESKTOPS)" --jobs "$(or $(JOBS),0)"

# The read-only share every VM mounts at /run/sentinel (DESIGN.md §9).
$(BUILD)/share/sentineld-agent: $(AGENT_SRC) core/util/json.hpp core/util/paths.hpp $(SRC)/$(ZIG)/zig $(GUEST_ROOT)/usr/lib/libxkbcommon.a
	@mkdir -p $(@D) && $(GUEST_CXX) -o $@ $(AGENT_SRC) $(GUEST_ROOT)/usr/lib/libxkbcommon.a

$(BUILD)/share/windows/sentineld-agent.exe: guest/agent/windows/agent.cpp core/util/json.cpp core/util/json.hpp $(SRC)/$(ZIG)/zig
	@mkdir -p $(@D) && $(WINDOWS_CXX) -o $@ guest/agent/windows/agent.cpp core/util/json.cpp -lws2_32 -ladvapi32 \
		-luser32 -lwtsapi32 -luserenv

# Windows guests' Vulkan driver (DESIGN.md §16 "Vulkan (Windows)"): Mesa's Venus ICD from a pinned
# osy/virtio-win-mesa and guest/vulkan/patches, cross-compiled with zig for ARM64 and for x64 (x64
# applications under emulation); their loader manifests beside them. A pattern rule: one run makes both.
$(BUILD)/share/windows/%/vulkan_virtio.dll $(BUILD)/share/windows/%/vulkan_virtio_x64.dll: \
		$(wildcard guest/vulkan/* guest/vulkan/patches/*) $(SRC)/$(ZIG)/zig
	@python3 guest/vulkan/build.py --zig $(SRC)/$(ZIG)/zig --work $(SRC)/vulkan --out $(@D)

# Windows guests' POSIX userland (DESIGN.md §9): MSYS2's base and packages from guest/msys2/sources.txt,
# downloaded once into build/src/msys2 and verified; `version` (the list's hash) tells the guest's
# userland.cmd whether it has them all. Files the list no longer names leave the share.
$(BUILD)/share/windows/msys2/version: guest/msys2/sources.txt
	@mkdir -p $(SRC)/msys2 $(@D)
	@grep -v '^#' $< | while read -r sum url; do f=$(SRC)/msys2/$$(basename "$$url"); \
		[ -f "$$f" ] || { curl -fsSL -o "$$f.part" "$$url" && echo "$$sum  $$f.part" | shasum -a 256 -c --quiet && mv "$$f.part" "$$f"; } || exit 1; \
		cp -c "$$f" $(@D)/ 2>/dev/null || cp "$$f" $(@D)/; done
	@cd $(@D) && for f in *; do [ "$$f" = version ] || grep -q "/$$f\$$" $(CURDIR)/$< || rm -f "$$f"; done
	@shasum -a 256 $< | cut -c1-64 > $@

$(SRC)/$(ZIG)/zig:
	@mkdir -p $(SRC) && curl -fsSL -o $(SRC)/$(ZIG).tar.xz.part $(ZIG_URL)
	@echo "$(ZIG_SHA256)  $(SRC)/$(ZIG).tar.xz.part" | shasum -a 256 -c --quiet
	@tar -xJf $(SRC)/$(ZIG).tar.xz.part -C $(SRC) && rm $(SRC)/$(ZIG).tar.xz.part && touch $@

# Alpine's libxkbcommon headers and static library: the guest agent's one dependency.
$(GUEST_ROOT)/usr/lib/libxkbcommon.a:
	@mkdir -p $(GUEST_ROOT)
	@for p in "$(XKB_DEV) $(XKB_DEV_SHA256)" "$(XKB_STATIC) $(XKB_STATIC_SHA256)"; do set -- $$p; \
		curl -fsSL -o $(SRC)/$$1.part $(XKB_URL)/$$1 && echo "$$2  $(SRC)/$$1.part" | shasum -a 256 -c --quiet && \
		tar -xzf $(SRC)/$$1.part -C $(GUEST_ROOT) 2>/dev/null; rm -f $(SRC)/$$1.part; done
	@test -f $@ && touch $@

# The catalog and the desktop session (DESIGN.md §8), read from the share by sentineld and the guest.
$(BUILD)/share/%: guest/%
	@mkdir -p $(@D) && cp -p $< $@

# The release the desktop app downloads (DESIGN.md §24): build/release/sentineld-<sha256[:16]>.tar.gz
# and sentineld.json, from the binaries, tools, share and distributions it builds first.
release: $(BUILD)/bin/sentineld $(BUILD)/bin/sentineld-media tools share distributions
	@rm -rf $(BUILD)/release && mkdir -p $(BUILD)/release
	@COPYFILE_DISABLE=1 tar -czf $(BUILD)/release/sentineld.tar.gz --exclude=__pycache__ --exclude=.DS_Store \
		-C $(BUILD) bin/sentineld bin/sentineld-media tools/mke2fs tools/resize2fs tools/wimlib-imagex tools/mkntfs tools/hive share distributions
	@sha=$$(shasum -a 256 $(BUILD)/release/sentineld.tar.gz | cut -c1-64); file=sentineld-$$(echo $$sha | cut -c1-16).tar.gz; \
		mv $(BUILD)/release/sentineld.tar.gz $(BUILD)/release/$$file; \
		printf '{"name": "sentineld", "file": "%s", "sha256": "%s"}\n' $$file $$sha > $(BUILD)/release/sentineld.json; \
		echo "$(BUILD)/release/$$file"

# Editor integration (clangd): the compile command of every source, from the flags above.
$(BUILD)/compile_commands.json: Makefile $(wildcard core/*.cpp core/*/*.cpp macos/backend/*.cpp media/*.cpp tests/e2e/*.cpp)
	@mkdir -p $(@D) && { echo '['; sep=''; for f in $(wildcard core/*.cpp core/*/*.cpp macos/backend/*.cpp media/*.cpp tests/e2e/*.cpp); do \
		printf '%s{"directory":"%s","file":"%s","command":"%s %s -I%s/$(DEPS)/include -c %s"}\n' "$$sep" "$(CURDIR)" "$$f" "$(CXX)" "$(CXXFLAGS)" "$(CURDIR)" "$$f"; \
		sep=','; done; echo ']'; } > $@

$(SRC)/$(E2FS).tar.xz:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $(E2FS_URL)
	@echo "$(E2FS_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@

$(SRC)/$(ARCHIVE).tar.xz:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $(ARCHIVE_URL)
	@echo "$(ARCHIVE_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@

# One configured e2fsprogs tree builds both helpers: install steps make root file systems with mke2fs,
# resize2fs grows a workspace's root file system before it boots (make 3.81 has no grouped targets:
# resize2fs is the rule, mke2fs follows from it).
$(TOOLS)/mke2fs: $(TOOLS)/resize2fs
	@test -f $@
$(TOOLS)/resize2fs: $(SRC)/$(E2FS).tar.xz $(SRC)/$(ARCHIVE).tar.xz
	rm -rf $(SRC)/$(E2FS) $(SRC)/$(ARCHIVE) && tar -xf $(SRC)/$(E2FS).tar.xz -C $(SRC) && tar -xf $(SRC)/$(ARCHIVE).tar.xz -C $(SRC)
	cd $(SRC)/$(E2FS) && ./configure --quiet --disable-nls --disable-fuse2fs --disable-debugfs --disable-imager \
		--disable-defrag --disable-tdb \
		CPPFLAGS="-I$(CURDIR)/$(SRC)/$(ARCHIVE)/libarchive" ARCHIVE_LIBS=-larchive
	$(MAKE) -s -C $(SRC)/$(E2FS) libs && $(MAKE) -s -C $(SRC)/$(E2FS)/misc mke2fs && $(MAKE) -s -C $(SRC)/$(E2FS)/resize resize2fs
	@mkdir -p $(@D) && cp $(SRC)/$(E2FS)/misc/mke2fs $(@D)/mke2fs && cp $(SRC)/$(E2FS)/resize/resize2fs $@

$(SRC)/$(WIMLIB).tar.gz:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $(WIMLIB_URL)
	@echo "$(WIMLIB_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@

# With NTFS-3G (static, below): wimlib-imagex also applies an image to an NTFS volume.
$(TOOLS)/wimlib-imagex: $(SRC)/$(WIMLIB).tar.gz $(TOOLS)/mkntfs
	rm -rf $(SRC)/$(WIMLIB) && tar -xf $< -C $(SRC)
	cd $(SRC)/$(WIMLIB) && ./configure --quiet --with-ntfs-3g --without-fuse --disable-shared \
		LIBNTFS_3G_CFLAGS="-I$(CURDIR)/$(SRC)/ntfs-3g/include" \
		LIBNTFS_3G_LIBS="-L$(CURDIR)/$(SRC)/$(NTFS3G)/libntfs-3g/.libs -lntfs-3g" LIBS="-framework CoreFoundation"
	$(MAKE) -s -C $(SRC)/$(WIMLIB)
	@mkdir -p $(@D) && cp $(SRC)/$(WIMLIB)/wimlib-imagex $@

$(SRC)/$(NTFS3G).tgz:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $(NTFS3G_URL)
	@echo "$(NTFS3G_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@

# The library (for wimlib) and mkntfs; no FUSE driver.
$(TOOLS)/mkntfs: $(SRC)/$(NTFS3G).tgz $(wildcard patches/ntfs-3g-*.patch)
	rm -rf $(SRC)/$(NTFS3G) $(SRC)/ntfs-3g && tar -xzf $< -C $(SRC)
	for p in patches/ntfs-3g-*.patch; do patch --batch --forward --fuzz=0 -s -d $(SRC)/$(NTFS3G) -p1 -i $(CURDIR)/$$p; done
	cd $(SRC)/$(NTFS3G) && ./configure --quiet --disable-ntfs-3g --enable-ntfsprogs --disable-shared --enable-static \
		--disable-crypto --without-uuid --without-hd
	$(MAKE) -s -C $(SRC)/$(NTFS3G) >/dev/null
	@mkdir -p $(SRC)/ntfs-3g/include && cp -R $(SRC)/$(NTFS3G)/include/ntfs-3g $(SRC)/ntfs-3g/include/
	@mkdir -p $(@D) && cp $(SRC)/$(NTFS3G)/ntfsprogs/mkntfs $@

$(SRC)/$(HIVEX).tar.gz:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $(HIVEX_URL)
	@echo "$(HIVEX_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@
$(SRC)/$(HIVEX)/lib/.libs/libhivex.a: $(SRC)/$(HIVEX).tar.gz
	rm -rf $(SRC)/$(HIVEX) && tar -xzf $< -C $(SRC)
	cd $(SRC)/$(HIVEX) && ./configure --quiet --disable-shared --enable-static --disable-ocaml --disable-perl \
		--disable-python --disable-ruby
	$(MAKE) -s -C $(SRC)/$(HIVEX)/gnulib/lib >/dev/null 2>&1 && $(MAKE) -s -C $(SRC)/$(HIVEX)/lib >/dev/null 2>&1

# The offline registry editor install steps run (core/tools/hive.cpp).
$(TOOLS)/hive: core/tools/hive.cpp core/util/json.cpp core/util/json.hpp $(SRC)/$(HIVEX)/lib/.libs/libhivex.a
	@mkdir -p $(@D) && $(CXX) $(CXXFLAGS) -I$(SRC)/$(HIVEX)/include -o $@ core/tools/hive.cpp core/util/json.cpp \
		$(SRC)/$(HIVEX)/lib/.libs/libhivex.a -liconv

$(SRC)/$(FATFS).zip:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $(FATFS_URL)
	@echo "$(FATFS_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@

# FatFs reads its options from the ffconf.h next to ff.h: ours replaces the stock one.
$(SRC)/$(FATFS)/source/ff.h: $(SRC)/$(FATFS).zip core/image/ffconf.h
	rm -rf $(SRC)/$(FATFS) && mkdir -p $(SRC)/$(FATFS) && unzip -q $< -d $(SRC)/$(FATFS)
	cp core/image/ffconf.h $(SRC)/$(FATFS)/source/ffconf.h && touch $@
$(BUILD)/obj/%.o: $(SRC)/$(FATFS)/source/ff.h
	@mkdir -p $(@D) && clang -O2 -c -o $@ $(SRC)/$(FATFS)/source/$*.c

# Pinned sources: one rule fetches and verifies any of them.
$(SRC)/%.tar.gz $(SRC)/%.tar.xz:
	@mkdir -p $(@D) && curl -fsSL -o $@.part $($(if $(findstring openssl,$*),OPENSSL,NGTCP2)_URL)
	@echo "$($(if $(findstring openssl,$*),OPENSSL,NGTCP2)_SHA256)  $@.part" | shasum -a 256 -c --quiet && mv $@.part $@

deps: $(DEPS)/lib/libngtcp2_crypto_ossl.a
$(DEPS)/lib/libssl.a: $(SRC)/$(OPENSSL).tar.gz
	rm -rf $(SRC)/$(OPENSSL) && tar -xf $< -C $(SRC)
	cd $(SRC)/$(OPENSSL) && ./Configure darwin64-arm64-cc no-shared no-tests no-docs no-apps no-legacy \
		--prefix=$(CURDIR)/$(DEPS) --libdir=lib >/dev/null
	$(MAKE) -s -C $(SRC)/$(OPENSSL) >/dev/null && $(MAKE) -s -C $(SRC)/$(OPENSSL) install_sw >/dev/null
$(DEPS)/lib/libngtcp2_crypto_ossl.a: $(SRC)/$(NGTCP2).tar.xz $(DEPS)/lib/libssl.a
	rm -rf $(SRC)/$(NGTCP2) && tar -xf $< -C $(SRC)
	cd $(SRC)/$(NGTCP2) && ./configure --quiet --enable-lib-only --disable-shared --with-openssl \
		--prefix=$(CURDIR)/$(DEPS) OPENSSL_CFLAGS=-I$(CURDIR)/$(DEPS)/include \
		OPENSSL_LIBS="-L$(CURDIR)/$(DEPS)/lib -lssl -lcrypto"
	$(MAKE) -s -C $(SRC)/$(NGTCP2) install >/dev/null

clean:
	rm -rf $(BUILD)

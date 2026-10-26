# sentinel-workspace: one workspace's virtual machine on macOS, started by sentineld (sentineld
# DESIGN.md §15). build/ is the directory sentineld takes with --workspace:
#   bin/sentinel-workspace   the VM process (libkrun on Hypervisor.framework)
#   lib/                     libkrun, virglrenderer (vrend, Venus, Neptune), ANGLE, libepoxy, DXMT,
#                            KosmicKrisp, the Vulkan loader, libslirp
#   share/firmware/          Sentinel's EFI firmware (edk2)
# Sources and tools are pinned and built here (macos/scripts/build.py); nothing is installed on the
# host. Meson needs Python, libkrun a private Rust toolchain; no Metal compiler is needed.

BUILD := build
WORK := $(BUILD)/work
LIB := $(BUILD)/lib
export SDKROOT := $(shell xcrun --sdk macosx --show-sdk-path)

.PHONY: all libs test release clean
all: $(BUILD)/bin/sentinel-workspace $(BUILD)/compile_commands.json
libs: $(LIB)/libkrun.dylib

$(LIB)/libkrun.dylib: $(wildcard macos/scripts/*) $(wildcard macos/patches/*)
	@python3 macos/scripts/build.py $(WORK) $(BUILD)

SRC := $(wildcard macos/workspace/*.m macos/workspace/*.c) macos/video/DesktopVideoEncoder.m
CFLAGS := -std=gnu11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -fobjc-arc \
	-Imacos/workspace -Imacos/video -I$(BUILD)/include
$(BUILD)/bin/sentinel-workspace: $(LIB)/libkrun.dylib $(SRC) $(wildcard macos/workspace/*.h macos/video/*.h) macos/workspace/entitlements.plist
	@mkdir -p $(@D) && clang $(CFLAGS) -o $@.new $(SRC) -L$(LIB) -lkrun -lslirp.0 -Wl,-rpath,@executable_path/../lib \
		$(addprefix -framework ,Foundation AudioToolbox CoreMedia CoreVideo CoreGraphics ImageIO QuartzCore VideoToolbox)
	@codesign --force --sign - --entitlements macos/workspace/entitlements.plist $@.new
	@mv -f $@.new $@  # a new file: running VMs keep the one they started with

# The encoder's backpressure, against VideoToolbox, and virglrenderer's renderers (no VM).
test:
	@mkdir -p $(BUILD)/tests && clang -fobjc-arc -Imacos/video -o $(BUILD)/tests/encoder-backpressure \
		macos/tests/encoder-backpressure.m $(addprefix -framework ,Foundation CoreMedia CoreVideo QuartzCore VideoToolbox) && \
		$(BUILD)/tests/encoder-backpressure && echo "PASS encoder-backpressure"
	@clang -fobjc-arc -std=gnu11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -DVIRGL_RENDERER_UNSTABLE_APIS \
		-I$(WORK)/local/include/virgl -I$(WORK)/virglrenderer/src -o $(BUILD)/tests/renderer-smoke \
		macos/tests/renderer-smoke.m -L$(LIB) -lvirglrenderer.1 -Wl,-rpath,@executable_path/../lib \
		$(addprefix -framework ,Foundation Metal) && \
		VK_DRIVER_FILES=$(CURDIR)/$(LIB)/kosmickrisp_icd.json $(BUILD)/tests/renderer-smoke && echo "PASS renderer-smoke"

# The release the desktop app downloads (sentineld DESIGN.md §24): build/release/
# sentinel-workspace-<sha256[:16]>.tar.gz and sentinel-workspace.json.
release: $(BUILD)/bin/sentinel-workspace
	@rm -rf $(BUILD)/release && mkdir -p $(BUILD)/release
	@COPYFILE_DISABLE=1 tar -czf $(BUILD)/release/sentinel-workspace.tar.gz --exclude=.DS_Store \
		-C $(BUILD) bin/sentinel-workspace lib share/firmware
	@sha=$$(shasum -a 256 $(BUILD)/release/sentinel-workspace.tar.gz | cut -c1-64); \
		file=sentinel-workspace-$$(echo $$sha | cut -c1-16).tar.gz; \
		mv $(BUILD)/release/sentinel-workspace.tar.gz $(BUILD)/release/$$file; \
		printf '{"name": "sentinel-workspace", "file": "%s", "sha256": "%s"}\n' $$file $$sha > $(BUILD)/release/sentinel-workspace.json; \
		echo "$(BUILD)/release/$$file"

# Editor integration (clangd).
$(BUILD)/compile_commands.json: Makefile $(SRC)
	@mkdir -p $(@D) && { echo '['; sep=''; for f in $(SRC); do \
		printf '%s{"directory":"%s","file":"%s","command":"clang %s -c %s"}\n' "$$sep" "$(CURDIR)" "$$f" '$(CFLAGS)' "$$f"; \
		sep=','; done; echo ']'; } > $@

clean:
	rm -rf $(BUILD)/bin $(BUILD)/share $(BUILD)/lib $(BUILD)/include $(BUILD)/release $(BUILD)/tests

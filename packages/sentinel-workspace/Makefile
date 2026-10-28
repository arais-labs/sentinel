# The libraries `sentineld vm` runs one workspace's virtual machine on (sentineld DESIGN.md §15), built
# into build/, which ../sentineld bundles:
#   lib/             libkrun, virglrenderer (vrend, Venus, Neptune), ANGLE, libepoxy, DXMT,
#                    KosmicKrisp, the Vulkan loader, libslirp
#   include/         their headers sentineld compiles against
#   share/firmware/  Sentinel's EFI firmware (edk2)
# Sources and tools are pinned and built here (macos/scripts/build.py); nothing is installed on the
# host. Meson needs Python, libkrun a private Rust toolchain; no Metal compiler is needed.

BUILD := build
WORK := $(BUILD)/work
LIB := $(BUILD)/lib
export SDKROOT := $(shell xcrun --sdk macosx --show-sdk-path)

.PHONY: all libs test clean
all libs: $(LIB)/libkrun.dylib

$(LIB)/libkrun.dylib: $(wildcard macos/scripts/*) $(wildcard macos/patches/*)
	@python3 macos/scripts/build.py $(WORK) $(BUILD)

# virglrenderer's renderers (no VM).
test: $(LIB)/libkrun.dylib
	@mkdir -p $(BUILD)/tests && clang -fobjc-arc -std=gnu11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -DVIRGL_RENDERER_UNSTABLE_APIS \
		-I$(WORK)/local/include/virgl -I$(WORK)/virglrenderer/src -o $(BUILD)/tests/renderer-smoke \
		macos/tests/renderer-smoke.m -L$(LIB) -lvirglrenderer.1 -Wl,-rpath,@executable_path/../lib \
		$(addprefix -framework ,Foundation Metal) && \
		VK_DRIVER_FILES=$(CURDIR)/$(LIB)/kosmickrisp_icd.json $(BUILD)/tests/renderer-smoke && echo "PASS renderer-smoke"

clean:
	rm -rf $(BUILD)/bin $(BUILD)/share $(BUILD)/lib $(BUILD)/include $(BUILD)/release $(BUILD)/tests $(BUILD)/compile_commands.json

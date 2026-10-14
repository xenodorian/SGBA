# Native SGBA runtime. Targets: linux-x86_64 (development and tests), linux-aarch64 (R36S and other ArkOS devices), windows-x64.
#   make                      build/linux-x86_64/sgba
#   make TARGET=aarch64       cross build for the R36S, glibc 2.17 (needs zig: pip install ziglang)
#   make TARGET=windows       Windows x64 .exe (needs zig)
TARGET ?= x86_64
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
OUT := $(ROOT)/build/$(TARGET)
# The release targets use zig as the C compiler (pip install ziglang): it links against glibc 2.17, the oldest the R36S can have,
# and cross builds Windows without a separate toolchain. "aarch64-gcc" is the plain distro cross compiler (newer glibc, tests only).
ZIG ?= python3 -m ziglang cc
ifeq ($(TARGET),aarch64)
  CC := $(ZIG) -target aarch64-linux-gnu.2.17 -mcpu=cortex_a35      # the R36S SoC (RK3326): tuning only, same ISA as any aarch64
  LIBS := -ldl -lm -lpthread
  LDFLAGS := -s
else ifeq ($(TARGET),aarch64-gcc)
  CC := aarch64-linux-gnu-gcc
  LIBS := -ldl -lm -lpthread
else ifeq ($(TARGET),windows)
  CC := $(ZIG) -target x86_64-windows-gnu
  LIBS := -lm
  LDFLAGS := -s
  EXE := .exe
else
  CC ?= gcc
  LIBS := -ldl -lm -lpthread
endif
CFLAGS := -MMD -MP -O3 -fno-plt -g -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE -I$(ROOT)/src -I$(ROOT)/vendor/quickjs -I$(ROOT)/vendor/sdl2 -I$(ROOT)/vendor/stb
QJS_CFLAGS := -O3 -DNDEBUG -D_GNU_SOURCE -w
SRC := $(wildcard $(ROOT)/src/*.c)
OBJ := $(patsubst $(ROOT)/src/%.c,$(OUT)/%.o,$(SRC)) $(OUT)/shim_data.o $(OUT)/font_data.o $(OUT)/shell_data.o $(OUT)/overlay_data.o
QJS := $(addprefix $(OUT)/qjs_,quickjs.o libregexp.o libunicode.o dtoa.o)
SHIM := $(ROOT)/../sgba_js_shim.js

all: $(OUT)/sgba$(EXE)

-include $(wildcard $(OUT)/*.d)

$(OUT):
	mkdir -p $(OUT)

$(OUT)/embed: $(ROOT)/tools/embed.c | $(OUT)
	gcc -O1 -o $@ $<

$(OUT)/shim_data.c: $(SHIM) $(OUT)/embed
	$(OUT)/embed $(SHIM) shim_js > $@

$(OUT)/shim_data.o: $(OUT)/shim_data.c
	$(CC) $(QJS_CFLAGS) -c -o $@ $<

$(OUT)/font_data.c: $(ROOT)/data/freesansbold.ttf $(OUT)/embed
	$(OUT)/embed $< font_ttf > $@

$(OUT)/font_data.o: $(OUT)/font_data.c
	$(CC) $(QJS_CFLAGS) -c -o $@ $<

$(OUT)/shell_data.c: $(ROOT)/shell/shell.html $(OUT)/embed
	$(OUT)/embed $< shell_html > $@

$(OUT)/shell_data.o: $(OUT)/shell_data.c
	$(CC) $(QJS_CFLAGS) -c -o $@ $<

$(OUT)/overlay_data.c: $(ROOT)/shell/overlay.js $(OUT)/embed
	$(OUT)/embed $< overlay_js > $@

$(OUT)/overlay_data.o: $(OUT)/overlay_data.c
	$(CC) $(QJS_CFLAGS) -c -o $@ $<

$(OUT)/qjs_%.o: $(ROOT)/vendor/quickjs/%.c | $(OUT)
	$(CC) $(QJS_CFLAGS) -c -o $@ $<

BUILD_ID := $(shell git -C $(ROOT) rev-parse --short HEAD 2>/dev/null || echo dev)
$(OUT)/report.o: CFLAGS += -DSGBA_BUILD=\"$(BUILD_ID)\"

$(OUT)/%.o: $(ROOT)/src/%.c $(wildcard $(ROOT)/src/*.h) | $(OUT)
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/sgba$(EXE): $(OBJ) $(QJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LIBS)

clean:
	rm -rf $(ROOT)/build

.PHONY: all clean

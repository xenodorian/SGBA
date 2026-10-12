# Native SGBA runtime. Targets: linux-x86_64 (development and tests), linux-aarch64 (R36S and other ArkOS devices), windows-x64.
#   make                      build/linux-x86_64/sgba
#   make TARGET=aarch64       cross build (needs aarch64-linux-gnu-gcc)
TARGET ?= x86_64
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
OUT := $(ROOT)/build/$(TARGET)
ifeq ($(TARGET),aarch64)
  CC := aarch64-linux-gnu-gcc
  LIBS := -ldl -lm -lpthread
else ifeq ($(TARGET),windows)
  CC := x86_64-w64-mingw32-gcc
  LIBS := -lm -static
  EXE := .exe
else
  CC ?= gcc
  LIBS := -ldl -lm -lpthread
endif
CFLAGS := -O2 -g -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE -I$(ROOT)/src -I$(ROOT)/vendor/quickjs -I$(ROOT)/vendor/sdl2 -I$(ROOT)/vendor/stb
QJS_CFLAGS := -O2 -D_GNU_SOURCE -w
SRC := $(wildcard $(ROOT)/src/*.c)
OBJ := $(patsubst $(ROOT)/src/%.c,$(OUT)/%.o,$(SRC)) $(OUT)/shim_data.o $(OUT)/font_data.o $(OUT)/shell_data.o
QJS := $(addprefix $(OUT)/qjs_,quickjs.o libregexp.o libunicode.o dtoa.o)
SHIM := $(ROOT)/../sgba_js_shim.js

all: $(OUT)/sgba$(EXE)

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

$(OUT)/qjs_%.o: $(ROOT)/vendor/quickjs/%.c | $(OUT)
	$(CC) $(QJS_CFLAGS) -c -o $@ $<

$(OUT)/%.o: $(ROOT)/src/%.c $(wildcard $(ROOT)/src/*.h) | $(OUT)
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/sgba$(EXE): $(OBJ) $(QJS)
	$(CC) -o $@ $^ $(LIBS)

clean:
	rm -rf $(ROOT)/build

.PHONY: all clean

#!/bin/sh
# Read-only device probe for the R36S (ArkOS). Installs nothing, writes nothing
# except stdout. Run only after the owner approves; redirect output to a file
# in a user-owned folder if a copy is wanted.
echo "== kernel/arch =="
uname -a
echo "== os-release =="
cat /etc/os-release 2>/dev/null | head -5
echo "== glibc =="
ldd --version 2>&1 | head -1
echo "== python =="
for p in python3 python; do
  if command -v "$p" >/dev/null 2>&1; then
    echo "$p: $(command -v "$p") $("$p" --version 2>&1)"
    "$p" -c "import pygame,sys;print('pygame',pygame.version.ver,'SDL',pygame.get_sdl_version())" 2>&1 | head -2
  else
    echo "$p: not found"
  fi
done
echo "== SDL2 libs =="
ls /usr/lib/aarch64-linux-gnu/libSDL2* /usr/lib/libSDL2* 2>/dev/null
echo "== memory =="
grep -E 'MemTotal|MemAvailable' /proc/meminfo
echo "== free disk (cwd) =="
df -h . 2>/dev/null | tail -1
echo "== input devices =="
grep -E 'Name=|Handlers=' /proc/bus/input/devices 2>/dev/null
echo "== gptokeyb/portmaster =="
command -v gptokeyb 2>/dev/null; ls /opt/system/Tools/PortMaster 2>/dev/null | head -5

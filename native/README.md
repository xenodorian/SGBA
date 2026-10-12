# SGBA native runtime

A C program that runs a web game folder (index.html plus its scripts) without Python.

    make                       # build/x86_64/sgba (development, tests)
    make TARGET=aarch64        # build/aarch64/sgba (R36S / ArkOS), glibc 2.17; needs zig: pip install ziglang
    make TARGET=windows        # build/windows/sgba.exe (needs zig)
    python ../tools/build_native_bundle.py arkos-aarch64 --web GAME --name "Name"   # self-contained folder + zip in ../dist

    sgba GAME_FOLDER           # window, pad and sound
    sgba                       # launcher: lists game folders under ./games
    sgba GAME --frames 600 --script "170:hold:right:300" --js "30=CODE" --shot out.png   # headless run

SDL2 is loaded at run time (`libSDL2-2.0.so.0`, or next to the program). `controls.json`, `sgba.log`, `saves/` and `games/`
live next to the executable.

Layout: `src/` runtime, `vendor/` QuickJS-ng, stb and SDL2 headers (types only), `shell/shell.html` the menu page,
`data/` the default font. `../sgba_js_shim.js` is embedded at build time and is shared with the Python runtime.
See `../NATIVE_TODO.md` for status.

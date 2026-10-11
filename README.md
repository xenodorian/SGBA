# SGBA

Super Game Boy Advanced: a homebrew-focused pseudo-emulator project.

## Emulator development baseline

The initial reference implementation is [gba-emulator by amyanger](https://github.com/amyanger/gba-emulator), a C Game Boy Advance emulator with a scanline-based renderer, a modular hardware layout, an SDL2 frontend, and headless frame-hash testing. Its MIT license permits modification and redistribution with attribution.

The source is tracked as a Git submodule at `vendor/gba-emulator`. Initialize it after cloning:

```sh
git clone --recurse-submodules https://github.com/xenodorian/SGBA.git
```

Or, after a normal clone:

```sh
git submodule update --init --recursive
```

## Development direction

Treat the upstream emulator as a reference baseline, not as proof that SGBA must reproduce all GBA hardware. Before changing the emulation model, document the intended SGBA hardware contract and identify which CPU, memory, graphics, input, audio, and cartridge features can be simplified for homebrew-only software.

Do not commit commercial ROMs, BIOS dumps, save files, or other copyrighted game assets. Supply your own legally obtained test inputs locally.

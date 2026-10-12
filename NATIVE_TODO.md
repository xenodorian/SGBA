# Native runtime to-do

Goal: one C program per platform (Windows PC, ArkOS R36S, Linux for development) that runs the same web game folder the browser runs.
No Python at run time. The Python runtime stays until the C one matches it (see "Retire Python" at the end).

Targets: **30 fps is the minimum, shippable**. **60 fps is the optimum.** Every millisecond saved is buffer above the 30 fps line.
Measure with `tools/device_sim.py` (ARM bundle under QEMU, one core, capped memory) and on the device with `sgba.log`.

Owner's rule: do the "can be done in C" list first, push and note it, then the three "costs real work" items one at a time,
pushing and noting after each.

## A. Can be done in C (do first)

- [ ] A1. Scaffold `native/`: vendored QuickJS-ng, stb (image, truetype, vorbis, image_write), SDL2 headers; Makefile for linux-x86_64 and aarch64
- [ ] A2. Host: load the game's `index.html`, run its scripts in QuickJS with `sgba_js_shim.js` (unchanged), timers and requestAnimationFrame on a fixed step
- [ ] A3. Window and display through SDL2 loaded at run time (dlopen), 640x480, scale to the screen, headless mode for tests (`--shot`, `--frames`)
- [ ] A4. Images: lazy PNG decode (stb_image), byte-budgeted LRU, colorkey/alpha/opaque kinds as in `sgba_gfx.py`
- [ ] A5. Canvas 2D in C: state, transforms, rects, paths, clip, drawImage, text, gradients, composite modes, retained main canvas with frame diff, groups
- [ ] A6. Audio: Ogg decode (stb_vorbis), SFX cache, streamed music, mixer in the SDL audio callback, the `__py_audio_*` commands
- [ ] A7. Input: keyboard events to the page, pad as the standard Gamepad, controls.json mapping, Start+Select
- [ ] A8. Saves (localStorage file), local file reads (fetch/XHR), preload hook, perf log
- [ ] A9. Menus and the controller setup as a small JavaScript page run by the same runtime (canvas drawn)
- [ ] A10. Checks: game boots to the title and plays level 1 with zero JS errors; speed numbers in the simulator; push and note

## B. Costs real work (one at a time, in this order)

- [ ] B1. HTML menus: lay out and draw the HTML/CSS subset the games use so buttons are pad-navigable (the overlay in `sgba_js_ui.py`)
- [ ] B2. Drawing parity: pixel comparison of the C canvas against the Python canvas on the 7 reference scenes, fix differences, set tolerances
- [ ] B3. R36S build baseline: old glibc, runs on ArkOS 2, cross build in CI, packaged like the current bundle (Windows build too)

## C. Retire Python (not yet; only on the owner's word)

- [ ] C1. Remove the Python runtime from both bundles once B2 passes and the device run is confirmed
- [ ] C2. Move the build and test tools off Python if wanted

## D. Later, after the emulator work (game changes, from the owner)

- [ ] D1. Intro (story) scenes: draw only the background that belongs to the scene, fitted to the screen. Today the title scene
      shows behind it, so two backgrounds appear (a story picture on the top part and the title background below).
- [ ] D2. Intro scenes: show only one Perry. Today the title Perry and the story's Perry are both drawn.
      (Both seen in the web build and on SGBA; fix in the game files, then bump the cache stamp.)

## Notes

(see the log at the bottom of `Current_Work.md` for dated entries)

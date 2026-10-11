# Browser Preview Container

This is a separate presentation wrapper for testing the shared runtime and game files in a phone browser. It is not a browser-specific game implementation and does not change the native desktop/R36S launch path.

The GitHub Pages workflow stages the repository's current `runtime.py` and entire `games/` directory beside this wrapper, then packages that staging directory with Pygbag (Python/Pygame WebAssembly). The browser therefore runs the same runtime module and game source files through a WebAssembly build. The wrapper supplies touch controls outside the game's 640×480 logical drawing surface.

## Controls

- D-pad and A/B/X/Y: movement and face-button actions.
- L1/R1 and L2/R2: shoulder and trigger actions.
- RS direction buttons and L3/R3: right-stick directions and stick clicks.
- Start and Select.
- Keyboard input is also passed through to the shared runtime.
- The top bar switches between available games under `games/*/main.py`.

## Deploy

The dedicated `.github/workflows/web-preview.yml` workflow builds and deploys this container to GitHub Pages when the runtime, game files, or preview wrapper changes, and supports manual dispatch. It does not alter the native runtime's setup, build, or test scripts.

Before the first deployment, open repository **Settings → Pages** and set **Build and deployment → Source** to **GitHub Actions**. The workflow's default `GITHUB_TOKEN` cannot enable Pages settings because that requires repository administration permission. Then run the **Web Preview** workflow from the Actions tab, or push a change to `runtime.py`, `games/`, or `web_preview/`. The preview is a browser/WebAssembly execution of the shared Python/Pygame runtime, not a test of the R36S firmware, native binary, or hardware-specific controller mapping. Native compatibility still requires testing on the handheld.

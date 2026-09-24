# Desktop previews

These are actual 1280 × 800 screenshots of disposable Sentinel workspaces,
not illustrations or upstream promotional images. XFCE/LXQt/Weston show Alpine;
GNOME shows GNOME 50 on Ubuntu 26.04 with Nautilus; Plasma shows Ubuntu 26.04
with Dolphin. Each capture uses Sentinel's
first-run defaults, running as the regular desktop user.
Distribution package versions, themes and user customization can change appearance.

Capture through the production computer-control screenshot path from
`apps/desktop/sentinel` after building the runtime:

```sh
SENTINEL_TEST_DESKTOP=xfce SENTINEL_DESKTOP_SCREENSHOT=/tmp/xfce-preview.png node tests/integration/host-desktop.mjs
```

Repeat with the desired desktop and distribution, choosing a new output filename
for each run. `SENTINEL_TEST_DISTRIBUTION=ubuntu SENTINEL_TEST_DESKTOP=gnome`
selects the GNOME preview's distribution and desktop.
The test creates and removes only its own disposable workspace. Inspect the
screenshots before replacing these assets; XFCE uses the backend's real appearance
defaults. Keep the image dimensions unchanged so the hover preview can reserve
space before the browser decodes the image.

GNOME was captured from the fresh disposable native-session qualification
workspace through the same production `Desktop.screenshot()` path after actual
Wayland/Xwayland pixel, keyboard, pointer, clipboard and audio checks. Source:
`/tmp/sentinel-desktop-ubuntu-gnome-final.png`; the full run, including restart
and shutdown, passed in `apps/desktop/sentinel/build/gnome-ubuntu-final-qualification.log`.
No image content was modified. Capture distribution labels live alongside their assets in
`workspaceDesktops.ts`; new choices need live qualification and a reviewed capture.

Plasma's unmodified capture is from
`apps/desktop/sentinel/build/plasma-ubuntu-native-qualified.png`, produced by the
fresh Ubuntu qualification run after first-login rendering, input, clipboard
and audio checks. A capture alone does not qualify session restart behavior;
the selectable desktop list is maintained separately.

LXQt's current capture is `/tmp/sentinel-desktop-lxqt-patched.png`, from the full
packaged Alpine qualification in
`apps/desktop/sentinel/build/lxqt-alpine-patched-qualification.log`. That run
verified the bundled labwc executable, normal-user session, Wayland/X11 input,
clipboard, audio, restart/resize and shutdown.

`tests/workspace-distribution.test.mjs` verifies every image loads and previews
work on hover and keyboard focus, without selecting a desktop or overflowing the
dialog. Set `SENTINEL_PREVIEW_SCREENSHOTS=/tmp/desktop-preview-ui` to capture the
tested desktop and narrow-window popup layouts.

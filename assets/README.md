# assets/

Static assets shipped with ClipWarden.

## Tray icons

- `icon.ico` - tray icon for the normal (enabled) state
- `icon-disabled.ico` - tray icon for the paused / disabled state
- `icon-alert.ico` - red alert variant shown for ~5 seconds after a
  detection fires, then swapped back to normal or disabled

All three are multi-resolution `.ico` files (16, 32, 48, 256 px
frames) so the tray, Alt-Tab switcher, and Settings-style surfaces
all render crisply. Source masters live outside the repo with the
rest of the brand artwork.

To replace the icons, drop new `.ico` files in this folder with the
same names. Keep the multi-resolution layout so HiDPI tray cells
have a native frame to pick up. If the filenames or paths change,
update [`build/ClipWarden.spec`](../build/ClipWarden.spec) and
[`build/installer.iss`](../build/installer.iss) to match.

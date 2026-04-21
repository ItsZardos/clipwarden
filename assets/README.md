# assets/

Static assets shipped with ClipWarden.

## Tray icons

- `icon.ico` - tray icon for the normal (enabled) state
- `icon-disabled.ico` - tray icon for the paused / disabled state

Both files are **placeholders**, generated deterministically by
[`tools/gen_icons.py`](../tools/gen_icons.py). The design is a
rounded shield silhouette with a horizontal clipboard-style slot cut
out near the top; monochrome white for the normal state, muted grey
for disabled.

A future designer should replace both files with a proper mark. When
they do:

1. Drop the new `icon.ico` and `icon-disabled.ico` in this folder.
   Keep the multi-resolution layout (16, 32, 48, 256 px) so the
   tray, Alt-Tab, and Settings-style surfaces all render crisply.
2. Delete `tools/gen_icons.py` (or point it at the new source SVG
   if the designer prefers a reproducible pipeline).
3. Update [`build/ClipWarden.spec`](../build/ClipWarden.spec) and
   [`build/installer.iss`](../build/installer.iss) if the filenames
   or paths change.

Regenerate the placeholders at any time with:

```powershell
python tools/gen_icons.py
```

The output is byte-stable across runs on the same Pillow release, so
commits stay clean unless the recipe itself changes.

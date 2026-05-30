# Karaoke extension icons — TODO

The PNG and SVG icons in this directory are **placeholders** copied from the
scribe extension and renamed for now. They unblock loading the unpacked
extension during development but should not ship to the Chrome Web Store.

Replace before any public release:

- `karaoke-16.png` (16x16, toolbar/menu)
- `karaoke-48.png` (48x48, extension management page)
- `karaoke-128.png` (128x128, install dialog / web store)
- `karaoke.svg` (source vector, optional)

When generating the final artwork, keep the same filenames so `manifest.json`
and `service_worker.js` (`NOTIFICATION_ICON`) keep working without edits.

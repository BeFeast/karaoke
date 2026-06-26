// Rasterize the mic mark (icons/mark.svg — geometry verbatim from the sign-in
// card, see the SVG header) into the extension action icons and the SPA
// favicon family. Run from extension/chrome:
//
//   bun install
//   bun run icons
//
// The extension icons (karaoke-*.png) drop the sign-in card plate (#177):
// transparent background, glyph only — scribe-style. The glyph stays
// single-sourced: the 24-viewBox mic SVG nested inside mark.svg is extracted
// verbatim (mark.svg geometry untouched) and re-rooted on a bare transparent
// canvas.
//
// The SPA favicon family (#163, #205) keeps the full sign-in card — a plate
// reads better in a browser tab, and #177's plate-drop is extension-only. It
// is the SAME sign as the extension icons (one source vector), just with the
// card retained. #205 widens the single 64x64 PNG into a full set so the tab
// icon stays crisp and the bare domain / iOS / PWA surfaces resolve:
//   favicon.png        64x64        legacy <link rel=icon>
//   favicon.ico        16/32/48     /favicon.ico (bare domain, bookmarks)
//   favicon.svg        scalable     crisp tab icon on modern browsers
//   apple-touch-icon   180x180      iOS home screen / Safari pinned tab
//   icon-192/icon-512  192/512      web-manifest (Android add-to-home-screen)
//
// resvg rasterizes the exact vector — nothing is redrawn per size, and the
// mark is pure geometry (no text nodes), so no fonts are involved (#163).
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Resvg } from "@resvg/resvg-js";

const here = dirname(fileURLToPath(import.meta.url));
const mark = readFileSync(join(here, "mark.svg"), "utf8");
const publicDir = join(here, "../../../web/spa/public");

// The nested mic glyph: <svg x=… y=… viewBox="0 0 24 24">…</svg>. Re-rooted on
// a 24-viewBox, its inherent margins (capsule top y=2, stand caps to y=22, arc
// strokes x=4..20) are the modest icon padding.
const nested = mark.match(/<svg [^>]*viewBox="0 0 24 24"[^>]*>([\s\S]*?)<\/svg>/);
if (!nested) {
  throw new Error("mark.svg: nested 24-viewBox mic glyph not found");
}
const glyph =
  '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" ' +
  `xmlns="http://www.w3.org/2000/svg" aria-label="Karaoke mark">${nested[1]}</svg>`;

// The day-card plate background (mark.svg :root, marquee.css Final bake). Baked
// behind the opaque surfaces (apple-touch + manifest icons) so iOS/Android
// never composite the card's transparent corners onto black.
const PLATE_BG = "#fbf9f4";

function renderPng(svg, size, opts = {}) {
  const resvg = new Resvg(svg, { fitTo: { mode: "width", value: size }, ...opts });
  return Buffer.from(resvg.render().asPng());
}

// Pack already-encoded PNGs into a single multi-resolution .ico. ICO entries
// may carry raw PNG payloads (Vista+), so no BMP re-encode is needed.
function pngToIco(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: 1 = icon
  header.writeUInt16LE(images.length, 4);
  const entries = [];
  const payloads = [];
  let offset = 6 + images.length * 16;
  for (const { size, data } of images) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size >= 256 ? 0 : size, 0); // width (0 means 256)
    entry.writeUInt8(size >= 256 ? 0 : size, 1); // height
    entry.writeUInt8(0, 2); // palette count
    entry.writeUInt8(0, 3); // reserved
    entry.writeUInt16LE(1, 4); // color planes
    entry.writeUInt16LE(32, 6); // bits per pixel
    entry.writeUInt32LE(data.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    payloads.push(data);
    offset += data.length;
  }
  return Buffer.concat([header, ...entries, ...payloads]);
}

// Extension toolbar icons — transparent glyph (#177). The 32 is rendered for
// completeness (#163); the manifest icons map still lists 16/48/128 only —
// Chrome scales those for the toolbar densities.
for (const size of [128, 48, 32, 16]) {
  const out = join(here, `karaoke-${size}.png`);
  writeFileSync(out, renderPng(glyph, size));
  console.log(`rendered ${out} (${size}x${size})`);
}

// SPA favicon family (#163, #205) — full sign-in card, single-sourced from mark.svg.
writeFileSync(join(publicDir, "favicon.png"), renderPng(mark, 64));
console.log("rendered favicon.png (64x64)");

const icoSizes = [16, 32, 48];
writeFileSync(
  join(publicDir, "favicon.ico"),
  pngToIco(icoSizes.map((size) => ({ size, data: renderPng(mark, size) }))),
);
console.log(`rendered favicon.ico (${icoSizes.join("/")})`);

writeFileSync(join(publicDir, "favicon.svg"), mark);
console.log("rendered favicon.svg");

writeFileSync(
  join(publicDir, "apple-touch-icon.png"),
  renderPng(mark, 180, { background: PLATE_BG }),
);
console.log("rendered apple-touch-icon.png (180x180)");

for (const size of [192, 512]) {
  writeFileSync(
    join(publicDir, `icon-${size}.png`),
    renderPng(mark, size, { background: PLATE_BG }),
  );
  console.log(`rendered icon-${size}.png (${size}x${size})`);
}

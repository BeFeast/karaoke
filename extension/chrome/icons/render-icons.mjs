// Rasterize the mic mark (icons/mark.svg — geometry verbatim from the sign-in
// card, see the SVG header) into the extension action icons and the SPA
// favicon. Run from extension/chrome:
//
//   bun install
//   bun run icons
//
// The extension icons (karaoke-*.png) drop the sign-in card plate (#177):
// transparent background, glyph only — scribe-style. The glyph stays
// single-sourced: the 24-viewBox mic SVG nested inside mark.svg is extracted
// verbatim (mark.svg geometry untouched) and re-rooted on a bare transparent
// canvas. The SPA favicon keeps the full sign-in card (#163) — a plate reads
// better in a browser tab, and #177's plate-drop is extension-only.
//
// resvg rasterizes the exact vector — nothing is redrawn per size, and the
// mark is pure geometry (no text nodes), so no fonts are involved (#163).
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Resvg } from "@resvg/resvg-js";

const here = dirname(fileURLToPath(import.meta.url));
const mark = readFileSync(join(here, "mark.svg"), "utf8");

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

const targets = [
  { svg: glyph, size: 128, out: join(here, "karaoke-128.png") },
  { svg: glyph, size: 48, out: join(here, "karaoke-48.png") },
  // 32 is rendered for completeness (#163); the manifest icons map still
  // lists 16/48/128 only — Chrome scales those for the toolbar densities.
  { svg: glyph, size: 32, out: join(here, "karaoke-32.png") },
  { svg: glyph, size: 16, out: join(here, "karaoke-16.png") },
  // The SPA favicon — still the full sign-in card (#163).
  { svg: mark, size: 64, out: join(here, "../../../web/spa/public/favicon.png") },
];

for (const { svg, size, out } of targets) {
  const resvg = new Resvg(svg, { fitTo: { mode: "width", value: size } });
  writeFileSync(out, resvg.render().asPng());
  console.log(`rendered ${out} (${size}x${size})`);
}

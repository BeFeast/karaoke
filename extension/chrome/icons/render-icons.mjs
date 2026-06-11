// Rasterize the sign-in mic mark (icons/mark.svg — geometry verbatim from the
// sign-in card, see the SVG header) into the extension action icons and the
// SPA favicon. Run from extension/chrome:
//
//   bun install
//   bun run icons
//
// resvg rasterizes the exact vector — nothing is redrawn per size, and the
// mark is pure geometry (no text nodes), so no fonts are involved (#163).
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Resvg } from "@resvg/resvg-js";

const here = dirname(fileURLToPath(import.meta.url));
const svg = readFileSync(join(here, "mark.svg"), "utf8");

const targets = [
  { size: 128, out: join(here, "karaoke-128.png") },
  { size: 48, out: join(here, "karaoke-48.png") },
  // 32 is rendered for completeness (#163); the manifest icons map still
  // lists 16/48/128 only — Chrome scales those for the toolbar densities.
  { size: 32, out: join(here, "karaoke-32.png") },
  { size: 16, out: join(here, "karaoke-16.png") },
  // The SPA favicon — same sign, no special case (#163, same carve-out as #155).
  { size: 64, out: join(here, "../../../web/spa/public/favicon.png") },
];

for (const { size, out } of targets) {
  const resvg = new Resvg(svg, { fitTo: { mode: "width", value: size } });
  writeFileSync(out, resvg.render().asPng());
  console.log(`rendered ${out} (${size}x${size})`);
}

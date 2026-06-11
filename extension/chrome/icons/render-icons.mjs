// Rasterize the MarqueeMark (icons/karaoke.svg — geometry verbatim from the
// design export, see the SVG header) into the extension action icons and the
// SPA favicon. Run from extension/chrome:
//
//   bun install
//   uvx --from fonttools --with brotli fonttools ttLib.woff2 decompress \
//     -o /tmp/bungee-latin-400.ttf \
//     ../../web/spa/node_modules/@fontsource/bungee/files/bungee-latin-400-normal.woff2
//   bun run icons
//
// The Bungee TTF (decompressed from the same fontsource woff2 the SPA and the
// doorway pages self-host) renders the letter; resvg rasterizes the exact
// vector — nothing is redrawn per size.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Resvg } from "@resvg/resvg-js";

const here = dirname(fileURLToPath(import.meta.url));
const svg = readFileSync(join(here, "karaoke.svg"), "utf8");
const fontPath = process.env.BUNGEE_TTF || "/tmp/bungee-latin-400.ttf";

const targets = [
  { size: 128, out: join(here, "karaoke-128.png") },
  { size: 48, out: join(here, "karaoke-48.png") },
  { size: 16, out: join(here, "karaoke-16.png") },
  // The SPA favicon — same sign, no special case (design/m-doorway.jsx:143).
  { size: 64, out: join(here, "../../../web/spa/public/favicon.png") },
];

for (const { size, out } of targets) {
  const resvg = new Resvg(svg, {
    fitTo: { mode: "width", value: size },
    font: {
      fontFiles: [fontPath],
      loadSystemFonts: false,
      defaultFontFamily: "Bungee",
    },
  });
  writeFileSync(out, resvg.render().asPng());
  console.log(`rendered ${out} (${size}x${size})`);
}

// Rasterize the shared Sonereli mark into Chrome, browser and PWA icons.
// Run `bun run icons` from extension/chrome.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Resvg } from "@resvg/resvg-js";

const here = dirname(fileURLToPath(import.meta.url));
const mark = readFileSync(join(here, "mark.svg"), "utf8");
const publicDir = join(here, "../../../web/spa/public");

const PLATE_BG = "#08090d";

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

// Keep the existing asset paths for installed clients.
for (const size of [128, 48, 32, 16]) {
  const out = join(here, `karaoke-${size}.png`);
  writeFileSync(out, renderPng(mark, size));
  console.log(`rendered ${out} (${size}x${size})`);
}

// SPA favicon family, single-sourced from mark.svg.
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

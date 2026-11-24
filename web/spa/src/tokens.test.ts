import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Sonereli UI/UX v1 step 0: the brand tokens in styles.css keep the contrast
// pairs the spec relies on (WCAG 2.x relative luminance, sRGB, solid surfaces).
const css = readFileSync(join(import.meta.dir, "styles.css"), "utf8");

function token(name: string): string {
  const match = new RegExp(`--sn-${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(css);
  if (!match) throw new Error(`--sn-${name} is not a 6-digit token`);
  return match[1];
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground, background, minimum] — text roles need 4.5:1 on both dark
// surfaces; the field/button boundary needs the 3:1 non-text minimum.
const PAIRS: [string, string, number][] = [];
for (const surface of ["bg", "surface"]) {
  for (const text of ["text", "muted", "past", "pink", "cyan", "lime", "error", "success", "warning"]) {
    PAIRS.push([text, surface, 4.5]);
  }
  PAIRS.push(["boundary", surface, 3]);
}
PAIRS.push(["on-accent", "lime", 4.5]);
PAIRS.push(["text", "raised", 4.5], ["muted", "raised", 4.5], ["cyan", "raised", 4.5], ["pink", "raised", 4.5]);

describe("Sonereli design tokens", () => {
  test.each(PAIRS)("%s on %s ≥ %p:1", (fg, bg, min) => {
    expect(contrast(token(fg), token(bg))).toBeGreaterThanOrEqual(min);
  });

  test("matches the spec's reference ratios", () => {
    expect(contrast(token("text"), token("bg"))).toBeCloseTo(18.54, 1);
    expect(contrast(token("pink"), token("bg"))).toBeCloseTo(5.88, 1);
    expect(contrast(token("lime"), token("on-accent"))).toBeCloseTo(17.48, 1);
  });

  test("violet stays atmosphere-only (below text contrast on the background)", () => {
    expect(contrast(token("violet"), token("bg"))).toBeLessThan(4.5);
  });
});

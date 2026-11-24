// Sonereli UI/UX v1 icon set (24px grid, 2px stroke; play/pause are filled).
// Paths come from the approved mockup's sprite. Icons are decorative: the
// control that holds one carries the accessible name.

const PATHS = {
  "play": "<path d=\"M7.5 4.9v14.2a.9.9 0 0 0 1.37.77l11.1-7.1a.9.9 0 0 0 0-1.53L8.87 4.12A.9.9 0 0 0 7.5 4.9z\"/>",
  "pause": "<rect x=\"6\" y=\"4.5\" width=\"4.2\" height=\"15\" rx=\"1.2\"/><rect x=\"13.8\" y=\"4.5\" width=\"4.2\" height=\"15\" rx=\"1.2\"/>",
  "back5": "<path d=\"M4.5 12a7.5 7.5 0 1 0 2.2-5.3\"/><path d=\"M4.6 3.6v3.6h3.6\"/><text x=\"12.2\" y=\"15.6\" text-anchor=\"middle\" font-size=\"8.5\" font-weight=\"700\" fill=\"currentColor\" stroke=\"none\" font-family=\"Inter,sans-serif\">5</text>",
  "fwd5": "<path d=\"M19.5 12a7.5 7.5 0 1 1-2.2-5.3\"/><path d=\"M19.4 3.6v3.6h-3.6\"/><text x=\"11.8\" y=\"15.6\" text-anchor=\"middle\" font-size=\"8.5\" font-weight=\"700\" fill=\"currentColor\" stroke=\"none\" font-family=\"Inter,sans-serif\">5</text>",
  "plus": "<path d=\"M12 5v14M5 12h14\"/>",
  "x": "<path d=\"M6 6l12 12M18 6L6 18\"/>",
  "down": "<path d=\"M6 9l6 6 6-6\"/>",
  "right": "<path d=\"M9 6l6 6-6 6\"/>",
  "left": "<path d=\"M15 6l-6 6 6 6\"/>",
  "more": "<circle cx=\"5.5\" cy=\"12\" r=\"1.6\" fill=\"currentColor\"/><circle cx=\"12\" cy=\"12\" r=\"1.6\" fill=\"currentColor\"/><circle cx=\"18.5\" cy=\"12\" r=\"1.6\" fill=\"currentColor\"/>",
  "search": "<circle cx=\"10.5\" cy=\"10.5\" r=\"6.5\"/><path d=\"M15.5 15.5L20 20\"/>",
  "songs": "<path d=\"M3.5 6h10M3.5 11h10M3.5 16h6M18.5 4v11.5\"/><circle cx=\"16\" cy=\"16\" r=\"2.5\"/>",
  "catalog": "<rect x=\"4\" y=\"4\" width=\"7\" height=\"7\" rx=\"1.5\"/><rect x=\"13\" y=\"4\" width=\"7\" height=\"7\" rx=\"1.5\"/><rect x=\"4\" y=\"13\" width=\"7\" height=\"7\" rx=\"1.5\"/><rect x=\"13\" y=\"13\" width=\"7\" height=\"7\" rx=\"1.5\"/>",
  "settings": "<path d=\"M4 7h9M17 7h3M4 17h3M11 17h9\"/><circle cx=\"15\" cy=\"7\" r=\"2\"/><circle cx=\"9\" cy=\"17\" r=\"2\"/>",
  "mic": "<rect x=\"9\" y=\"3\" width=\"6\" height=\"11\" rx=\"3\"/><path d=\"M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21\"/>",
  "micoff": "<rect x=\"9\" y=\"3\" width=\"6\" height=\"11\" rx=\"3\"/><path d=\"M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M4 4l16 16\"/>",
  "link": "<path d=\"M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1\"/><path d=\"M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1\"/>",
  "upload": "<path d=\"M12 15V4M7.5 8.5L12 4l4.5 4.5M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15\"/>",
  "share": "<path d=\"M12 3v12M8 7l4-4 4 4M7 10H6a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-1\"/>",
  "check": "<path d=\"M5 12.5l4.5 4.5L19 7.5\"/>",
  "alert": "<path d=\"M12 4l9 16H3z\"/><path d=\"M12 10v4M12 17.2v.1\"/>",
  "retry": "<path d=\"M19.5 12a7.5 7.5 0 1 1-2.2-5.3\"/><path d=\"M19.4 3.6v3.6h-3.6\"/>",
  "text": "<path d=\"M3 18l5-12 5 12M4.8 14h6.4\"/><path d=\"M15.5 12.5c.6-1 1.6-1.5 2.8-1.5 1.8 0 2.7 1 2.7 2.8V18M21 15.2c-3.7-.3-5.6.4-5.6 1.7 0 .8.7 1.3 1.7 1.3 2 0 3.9-1.4 3.9-3\"/>",
  "expand": "<path d=\"M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5\"/>",
  "download": "<path d=\"M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14\"/>",
  "flag": "<path d=\"M5 21V4M5 4.5h11.5l-2 4 2 4H5\"/>",
  "edit": "<path d=\"M4 20h4L19 9l-4-4L4 16z\"/>",
  "note": "<path d=\"M9 18V6l10-2v12\"/><circle cx=\"6.5\" cy=\"18\" r=\"2.5\"/><circle cx=\"16.5\" cy=\"16\" r=\"2.5\"/>",
  "copy": "<rect x=\"8\" y=\"8\" width=\"12\" height=\"12\" rx=\"2\"/><path d=\"M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3\"/>",
  "msg": "<path d=\"M4 5h16v11H9l-5 4z\"/>",
} as const;

export type IconName = keyof typeof PATHS;

const FILLED = new Set<IconName>(["play", "pause"]);

export function Icon({ name, size = 24, className }: { name: IconName; size?: number; className?: string }) {
  const classes = ["sn-ic", FILLED.has(name) ? "sn-ic-fill" : "", className ?? ""].filter(Boolean).join(" ");
  return (
    <svg className={classes} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false"
      dangerouslySetInnerHTML={{ __html: PATHS[name] }} />
  );
}

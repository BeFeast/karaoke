// Pure source_url display helper (#173). Upload jobs (#172) carry the
// sentinel `upload://<filename>` in source_url instead of a real URL — the
// SPA must never render the raw sentinel as a title nor link it as an
// external source. The filename inside the sentinel is stored verbatim
// (server-sanitized, not percent-encoded), so the label is a plain
// prefix-strip with no decoding.

export const UPLOAD_PREFIX = "upload://";

export interface SourceDisplay {
  /** "upload" for `upload://` sentinels, "url" for everything else. */
  kind: "url" | "upload";
  /** Render-ready label: the uploaded filename, or the URL verbatim. */
  label: string;
}

/**
 * Classify a job's source_url for rendering. Total over any input — empty
 * or garbage values yield a safe label, never a throw. The prefix match is
 * case-sensitive on purpose: the server always writes lowercase `upload://`.
 */
export function sourceDisplay(sourceUrl: string): SourceDisplay {
  const raw = typeof sourceUrl === "string" ? sourceUrl : "";
  if (raw.startsWith(UPLOAD_PREFIX)) {
    const name = raw.slice(UPLOAD_PREFIX.length).trim();
    return { kind: "upload", label: name || "uploaded audio" };
  }
  return { kind: "url", label: raw };
}

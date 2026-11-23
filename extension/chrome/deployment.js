export const DEFAULT_BASE_URL = "https://sonereli.oklabs.uk";

const PUBLIC_HOSTS = new Set([new URL(DEFAULT_BASE_URL).host, "karaoke.oklabs.uk"]);

export function isBoothHost(host, configuredHost) {
  return host === configuredHost || PUBLIC_HOSTS.has(host);
}

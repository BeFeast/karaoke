// The verifier parses the actual serialized Request, including its binary
// file part, before returning a local receipt or forwarding one live POST.
(() => {
  const original = window.fetch.bind(window);
  const reports = [];
  window.karaokeVerifyFeedback = reports;

  async function inspectPng(file) {
    if (!(file instanceof File) || file.type !== "image/png" || file.size > 8 * 1024 * 1024) return false;
    const magic = new Uint8Array(await file.slice(0, 8).arrayBuffer()).join(",");
    if (magic !== "137,80,78,71,13,10,26,10") return false;
    const image = await createImageBitmap(file);
    try {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 32;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(image, 0, 0, 32, 32);
      const { data } = ctx.getImageData(0, 0, 32, 32);
      for (let i = 4; i < data.length; i += 4) {
        if (data[i + 3] && data.slice(i, i + 3).some((channel, n) => Math.abs(channel - data[n]) > 24)) {
          return true;
        }
      }
      return false;
    } finally {
      image.close();
    }
  }

  window.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    if (url.pathname !== "/api/feedback/karaoke" || method !== "POST") return original(input, init);
    const request = new Request(input, init);
    const form = await request.clone().formData();
    const meta = JSON.parse(form.get("meta"));
    reports.push({
      text: form.get("text"),
      kind: form.get("kind"),
      privateLog: typeof form.get("log") === "string",
      version: meta.version,
      screenshot: await inspectPng(form.get("screenshot")),
      installId: form.get("install_id"),
      noShareToken: !JSON.stringify(meta).includes(location.pathname.split("/").at(-1))
        && !Object.hasOwn(meta, "job_token"),
      noIdentity: [...form.keys()].every((key) => ["text", "kind", "meta", "screenshot", "log", "install_id"].includes(key))
        && !Object.keys(meta).some((key) => /(^|_)(name|email|clerk|user_id)(_|$)/i.test(key))
        && !request.headers.has("authorization") && request.credentials === "omit",
    });
    if (window.karaokeVerifyLiveFeedback) {
      if (reports.length === 1) return original(request);
      throw new Error("Only one live feedback POST is allowed per run");
    }
    const issue_number = 9000 + reports.length;
    return Response.json({ issue_number, issue_url: `https://git.oklabs.uk/BeFeast/karaoke/issues/${issue_number}` },
      { status: 201 });
  };
})();

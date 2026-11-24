import html2canvas from "html2canvas-pro";

const PNG_LIMIT = 8 * 1024 * 1024;
const MAX_EDGE = 1600;

export function captureViewport(): Promise<HTMLCanvasElement> {
  const { innerWidth, innerHeight, scrollX, scrollY } = window;
  return html2canvas(document.body, {
    x: scrollX,
    y: scrollY,
    width: innerWidth,
    height: innerHeight,
    scale: Math.min(1, MAX_EDGE / Math.max(innerWidth, innerHeight)),
    scrollX,
    scrollY,
    logging: false,
    imageTimeout: 2000,
    ignoreElements: (element) => element.hasAttribute("data-feedback-private")
      || element.tagName === "INPUT" || element.tagName === "TEXTAREA",
  });
}

export async function feedbackPng(screen: HTMLCanvasElement | null, photo: File | null): Promise<Blob | null> {
  if (!screen && !photo) return null;
  let canvas = screen;
  if (photo) {
    const image = await createImageBitmap(photo);
    try {
      const scale = Math.min(1, MAX_EDGE / Math.max(image.width, image.height));
      const width = Math.max(1, Math.round(image.width * scale));
      const height = Math.max(1, Math.round(image.height * scale));
      // The service accepts one PNG. Keep the screen and photo together in it.
      canvas = document.createElement("canvas");
      canvas.width = Math.max(width, screen?.width ?? 0);
      canvas.height = height + (screen?.height ?? 0);
      const ctx = canvas.getContext("2d")!;
      if (screen) ctx.drawImage(screen, (canvas.width - screen.width) / 2, 0);
      ctx.drawImage(image, (canvas.width - width) / 2, screen?.height ?? 0, width, height);
    } finally {
      image.close();
    }
  }
  while (canvas) {
    const png = await new Promise<Blob | null>((resolve) => canvas!.toBlob(resolve, "image/png"));
    if (!png) throw new Error("Could not encode the attachment.");
    if (png.size <= PNG_LIMIT) return png;
    const smaller = document.createElement("canvas");
    smaller.width = Math.max(1, Math.floor(canvas.width / 2));
    smaller.height = Math.max(1, Math.floor(canvas.height / 2));
    smaller.getContext("2d")!.drawImage(canvas, 0, 0, smaller.width, smaller.height);
    canvas = smaller;
  }
  return null;
}

// Runs before page scripts. Observe the Web Audio timeline and gains without
// replacing playback, bypassing the user gesture, or modifying the app state.
(() => {
  const routes = new WeakMap();
  const sources = new Map();
  const prototype = AudioBufferSourceNode.prototype;
  const connect = prototype.connect;
  const start = prototype.start;
  const stop = prototype.stop;
  prototype.connect = function (destination, ...args) {
    const result = connect.call(this, destination, ...args);
    if (destination instanceof GainNode) routes.set(this, destination);
    return result;
  };
  prototype.start = function (...args) {
    const result = start.apply(this, args);
    if (routes.has(this)) sources.set(this, { when: args[0] ?? 0, offset: args[1] ?? 0 });
    return result;
  };
  prototype.stop = function (...args) {
    const result = stop.apply(this, args);
    sources.delete(this);
    return result;
  };

  const select = (selector) => document.querySelector(selector);
  const all = (selector) => [...document.querySelectorAll(selector)];
  const play = () => select('button[aria-label="Play"], button[aria-label="Pause"]');
  const audio = () => select("audio[controls]");
  const mix = () => select('.ksplayer [style*="ew-resize"], .ksplayer [style*="ns-resize"]');
  const line = (row) => row.querySelector(".synced-lyrics-seek");

  function rect(element, offsetX = 0.5, offsetY = 0.5) {
    const box = element.getBoundingClientRect();
    return { x: box.left + offsetX * box.width, y: box.top + offsetY * box.height };
  }

  function hit(element, point) {
    const top = document.elementFromPoint(point.x, point.y);
    const box = element.getBoundingClientRect();
    return element.contains(top) && box.left >= -1 && box.right <= innerWidth + 1
      && Math.abs((visualViewport?.scale ?? 1) - 1) < 0.02;
  }

  window.karaokeVerify = {
    point(control, scroll = false, mixLevel = 0.5) {
      const element = control === "play" ? play() ?? audio() : mix();
      if (!element || element.disabled) return null;
      if (scroll) element.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
      // Chrome's native media controls place the play button at the left of a
      // visible audio control. DOM hit-testing retargets it to the audio node.
      let point = element === audio() ? rect(element, 22 / element.clientWidth) : rect(element);
      if (control === "mix") {
        point = element.style.cursor === "ns-resize" ? rect(element, 0.5, 1 - mixLevel)
          : rect(element, mixLevel, 0.5);
      }
      return { ...point, reachable: hit(element, point) };
    },

    snapshot() {
      const native = audio();
      const synced = select('[aria-label="Synced lyrics"]');
      const rows = synced ? [...synced.children] : [];
      const current = rows.find((row) => row.getAttribute("aria-current") === "true");
      const sung = rows.filter((row) => !row.classList.contains("is-speech"));
      const plain = select(".lyrics pre, .synced-lyrics-plain");
      const sizes = sung.length ? sung.map((row) => parseFloat(getComputedStyle(line(row)).fontSize))
        : plain ? [parseFloat(getComputedStyle(plain).fontSize)] : [];
      const hebrewRows = sung.filter((row) => /\p{Script=Hebrew}/u.test(line(row).textContent));
      const firstStrongHebrew = (value) => /\p{Script=Hebrew}/u.test(value.match(/\p{L}/u)?.[0] ?? "");
      const rtlRows = hebrewRows.filter((row) => firstStrongHebrew(line(row).textContent));
      const rtl = rtlRows.map((row) => getComputedStyle(line(row)).direction);
      const activeSources = [...sources.entries()].filter(([node]) => node.context.state === "running");
      const tracks = activeSources.map(([node, { when, offset }]) => ({
        time: offset + Math.max(0, node.context.currentTime - when) * node.playbackRate.value,
        gain: routes.get(node).gain.value,
        duration: node.buffer.duration,
      }));
      const viewport = document.documentElement.clientWidth;
      return {
        ready: !!(play() && !play().disabled || native),
        player: play() ? "web_audio" : native ? "native_audio" : null,
        error: select(".ksplayer-error")?.textContent ?? native?.error?.message ?? null,
        tracks,
        time: native ? native.currentTime : tracks[0]?.time ?? null,
        duration: native ? (Number.isFinite(native.duration) ? native.duration : null) : tracks[0]?.duration ?? null,
        active: current ? {
          index: rows.indexOf(current),
          text: line(current).textContent.trim(),
        } : null,
        lineCount: rows.length,
        minFontPx: sizes.length ? Math.min(...sizes) : null,
        activeFontPx: current ? parseFloat(getComputedStyle(line(current)).fontSize) : null,
        pageWidth: viewport,
        contentWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth ?? 0),
        lyricsWidth: synced?.clientWidth ?? plain?.clientWidth ?? null,
        lyricsScrollWidth: synced?.scrollWidth ?? plain?.scrollWidth ?? null,
        scale: visualViewport?.scale ?? null,
        mixPresent: !!mix(),
        rtlLines: rtl.filter((direction) => direction === "rtl").length,
        hebrewLines: rtlRows.length,
        lyricsDirection: synced ? getComputedStyle(synced).direction
          : plain ? getComputedStyle(plain).direction : null,
        plainHebrew: !!plain && /\p{Script=Hebrew}/u.test(plain.textContent),
        title: select("h1")?.textContent?.trim(),
        controls: all("audio[controls]").length,
      };
    },
  };
})();

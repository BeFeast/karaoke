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
  // Sonereli Stage (UI/UX v1 step 6) marks its controls with data-sn-*; the
  // aria-label fallbacks keep older releases and Performance view working.
  const play = () => select('[data-sn-play], button[aria-label="Play"], button[aria-label="Pause"]');
  const audio = () => select("audio[controls]");
  const mix = () => select('[data-sn-vocals], .ksplayer [style*="ew-resize"], .ksplayer [style*="ns-resize"]');
  const lyricsList = () => select('.synced-lyrics-list') ?? select('[aria-label="Synced lyrics"]');
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
    async seekLyric(index) {
      line(all(".synced-lyrics-line")[index]).click();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    },

    lyricLayout(performance) {
      const list = lyricsList();
      const rows = [...list.children];
      const current = rows.find((row) => row.getAttribute("aria-current") === "true");
      // Keep the old surface selectors so this scenario can also run against v0.45.0.
      const overlay = select('[aria-label="Exit performance mode"]')?.parentElement.parentElement;
      const legacyParts = [...(overlay?.querySelectorAll(".m-wipe .w-dim") ?? [])]
        .filter((part) => part.getClientRects().length);
      const legacyActive = legacyParts[0]?.closest(".m-wipe")?.parentElement.parentElement;
      const active = performance
        ? overlay?.querySelector("[data-lyric-active]") ?? legacyActive
        : current && line(current);
      const ladder = performance && (overlay?.querySelector(".performance-lyrics")
        ?? legacyActive?.parentElement.parentElement);
      const inactive = performance
        ? [...(ladder?.querySelectorAll("[data-lyric-neighbor], span[dir][style*='font-size']") ?? [])]
        : rows.filter((row) => row !== current).map(line);
      const inline = !performance && select(".ksplayer [data-lyric-active], .ksplayer .m-wipe");
      if (!performance) inactive.push(...all(".ksplayer [data-lyric-neighbor]"));
      const bounds = (element) => element?.getBoundingClientRect();
      const viewport = bounds(performance ? active?.closest(".performance-current") ?? ladder : list);
      const box = bounds(active);
      const glyphRows = (element) => {
        const range = document.createRange();
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        const rows = new Set();
        for (let text; (text = walker.nextNode());) {
          if (!text.textContent.trim()) continue;
          range.selectNodeContents(text);
          for (const box of range.getClientRects()) {
            if (box.width > 0) rows.add(Math.round(box.bottom / 4));
          }
        }
        return rows.size;
      };
      const font = (el) => parseFloat(getComputedStyle(el).fontSize);
      const overflow = [active, inline, ...inactive].filter(Boolean)
        .filter((el) => el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0);
      const words = active ? [...active.querySelectorAll("[data-lyric-word-start]")] : [];
      const lastStarted = words.findLastIndex((word) => parseFloat(
        word.style.getPropertyValue("--lyric-progress")) > 0);
      const tracks = [...sources.entries()].filter(([node]) => node.context.state === "running");
      const [node, source] = tracks[0] ?? [];
      return {
        index: rows.indexOf(current),
        text: legacyParts.length && performance
          ? legacyParts.map((part) => part.textContent.trim()).join(" ")
          : active?.textContent.trim() ?? null,
        activePx: active ? Math.min(font(active), inline ? font(inline) : Infinity) : null,
        inactivePx: inactive.length ? Math.min(...inactive.map(font)) : null,
        overflow: overflow.length,
        inView: !!(box && viewport && box.top >= Math.max(0, viewport.top) - 1
          && box.bottom <= Math.min(innerHeight, viewport.bottom) + 1
          && box.left >= -1 && box.right <= innerWidth + 1),
        // Top of the active line as a share of the lyrics viewport (step 7 anchor).
        anchor: !performance && box && viewport && viewport.height > 0
          ? Math.round(((box.top - viewport.top) / viewport.height) * 1000) / 1000 : null,
        heightShare: !performance && box && viewport && viewport.height > 0
          ? Math.round((box.height / viewport.height) * 1000) / 1000 : null,
        scrollTop: performance ? null : Math.round(list.scrollTop),
        visualRows: performance && legacyParts.length ? legacyParts.length : active ? glyphRows(active) : 0,
        inlineWraps: !inline || glyphRows(inline) > 1,
        direction: active ? getComputedStyle((performance && legacyParts[0]) || active).direction : null,
        wordCount: words.length,
        lastStarted,
        time: node ? source.offset
          + Math.max(0, node.context.currentTime - source.when) * node.playbackRate.value : null,
      };
    },

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
      const synced = lyricsList();
      const rows = synced ? [...synced.children] : [];
      const current = rows.find((row) => row.getAttribute("aria-current") === "true");
      const sung = rows.filter((row) => !row.classList.contains("is-speech"));
      const plain = select(".lyrics pre, .lyrics-text");
      const sizes = rows.length ? rows.filter((row) => row !== current)
        .map((row) => parseFloat(getComputedStyle(line(row)).fontSize))
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
      const currentBox = current?.getBoundingClientRect();
      const lyricsBox = synced?.getBoundingClientRect();
      const activeInView = !!(currentBox && lyricsBox &&
        currentBox.top >= Math.max(0, lyricsBox.top) - 1 &&
        currentBox.bottom <= Math.min(innerHeight, lyricsBox.bottom) + 1);
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
        activeInView,
        pageWidth: viewport,
        contentWidth: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth ?? 0),
        lyricsWidth: synced?.clientWidth ?? plain?.clientWidth ?? null,
        lyricsScrollWidth: synced?.scrollWidth ?? plain?.scrollWidth ?? null,
        scale: visualViewport?.scale ?? null,
        mixPresent: !!mix(),
        vocalsPct: mix()?.getAttribute("aria-valuenow") != null ? Number(mix().getAttribute("aria-valuenow")) : null,
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

// Mini-player (Sonereli UI/UX v1 step 8): the song keeps playing above the
// tabs. Cover, title, the line being sung, play/pause and a cyan hairline of
// progress. Tapping it opens the Stage on the same moment.

import { t } from "../i18n";
import { coverUrl } from "../lib/library";
import { useOptionalPlayerSession } from "../player/PlayerProvider";
import { itemHash } from "../router";
import { Icon } from "./Icon";
import { activeLineIndex } from "./SyncedLyrics";

export function MiniPlayer() {
  const ctx = useOptionalPlayerSession();
  if (!ctx?.session || ctx.session.shared) return null;
  const { session, player } = ctx;
  const index = activeLineIndex(session.lines, player.currentTime);
  const line = index >= 0 ? session.lines[index].text : session.artist ?? "";
  const cover = coverUrl(session.sourceUrl);
  const pct = player.duration > 0 ? Math.min(100, (player.currentTime / player.duration) * 100) : 0;
  return (
    <div className="sn-mini" data-sn-mini="">
      <a className="sn-mini-open" href={itemHash(session.token)} aria-label={t("mini.open", { title: session.title })}>
        {cover ? <img className="sn-mini-cover" src={cover} alt="" /> : <span className="sn-mini-cover" aria-hidden="true"><Icon name="note" size={22} /></span>}
        <span className="sn-grow">
          <bdi className="sn-t1" dir="auto">{session.title}</bdi>
          <bdi className="sn-t2 sn-mini-line" dir="auto">{line}</bdi>
        </span>
      </a>
      <button type="button" className="sn-iconbtn sn-mini-play" onClick={player.playPause} disabled={!player.ready}
        aria-label={player.playing ? t("stage.pause") : t("stage.play")} data-sn-mini-play="">
        <Icon name={player.playing ? "pause" : "play"} size={26} />
      </button>
      <span className="sn-mini-progress" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>
    </div>
  );
}

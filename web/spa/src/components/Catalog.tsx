// Catalog (#/catalog, #/catalog/:axis/:value; Sonereli UI/UX v1 step 5).
// Shelves of 148 pt cards (cover, title in two lines, artist, state), a 2-up
// grid for See all, and a result list for search that also shows the
// recording version. Prepare starts with one tap (no confirmation — Oleg,
// 2026-11-24) and the card turns into Preparing · N%, which opens Processing.

import { useEffect, useState } from "react";
import { type CatalogPage, type CatalogSong, getCatalog, type JobOut } from "../api";
import { t } from "../i18n";
import { statusMeta } from "../jobStatus";
import { youtubeId } from "../lib/songLink";
import { catalogHash, type CatalogShelf, itemHash } from "../router";
import { Icon } from "./Icon";

const PRIMARY = ["Hebrew hits", "English hits"];
const GENRE_NAMES: Record<string, string> = { pop: "Pop", "hip-hop": "Hip hop", mizrahi: "Mizrahi" };

function shelfTitle(shelf: CatalogShelf) {
  if (shelf.axis === "decade") return shelf.value + "s";
  return shelf.axis === "genre" ? GENRE_NAMES[shelf.value] ?? shelf.value : shelf.value;
}

type CardState = { kind: "ready" } | { kind: "preparing"; job: JobOut | null; pct: number } | { kind: "prepare" };

function Art({ song, ready }: { song: CatalogSong; ready: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <span className="sn-art">
      {song.thumbnail_url && !failed
        ? <img src={song.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
        : <Icon name="note" size={40} />}
      {ready && <span className="sn-badge-play" aria-hidden="true"><Icon name="play" size={20} /></span>}
    </span>
  );
}

function StateLabel({ state }: { state: CardState }) {
  if (state.kind === "ready") return <span className="sn-state is-ready">{t("catalog.ready")}</span>;
  if (state.kind === "preparing") {
    return <span className="sn-state is-busy">{state.job && state.pct > 0 ? t("catalog.preparing", { pct: state.pct }) : t("catalog.queued")}</span>;
  }
  return <span className="sn-state is-prepare">{t("catalog.prepare")}</span>;
}

function SongLink({ song, state, onPrepare, className, children }: {
  song: CatalogSong;
  state: CardState;
  onPrepare: (song: CatalogSong) => void;
  className: string;
  children: React.ReactNode;
}) {
  const vars = { title: song.title, artist: song.artist };
  const common = { className: `catalog-card ${className}`, "data-catalog-song": song.id, "data-state": state.kind };
  if (state.kind === "ready" && song.stage_token) {
    return <a {...common} href={itemHash(song.stage_token)} aria-label={t("catalog.singLabel", vars)}>{children}</a>;
  }
  if (state.kind === "preparing") {
    return state.job
      ? <a {...common} href={itemHash(state.job.job_token)} aria-label={t("catalog.preparingLabel", vars)}>{children}</a>
      : <span {...common} aria-label={t("catalog.preparingLabel", vars)}>{children}</span>;
  }
  return <button {...common} type="button" onClick={() => onPrepare(song)} aria-label={t("catalog.prepareLabel", vars)}>{children}</button>;
}

export function CatalogBrowser({ query, shelf, jobs, prepare, onAdd }: {
  query: string;
  shelf: CatalogShelf | null;
  /** The owner's jobs: a catalog song being prepared shows its progress. */
  jobs: JobOut[];
  prepare: (url: string) => Promise<unknown>;
  onAdd: () => void;
}) {
  // The page is tagged with the request it answers, so a new query never
  // renders the previous shelf as its results for a frame.
  const [loadedPage, setLoadedPage] = useState<{ key: string; page: CatalogPage } | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [pending, setPending] = useState<Set<number>>(() => new Set());
  const [prepareError, setPrepareError] = useState(false);
  const search = query.trim();
  const axis = search ? undefined : shelf?.axis;
  const value = search ? undefined : shelf?.value;
  const key = JSON.stringify([search, axis ?? null, value ?? null, retry]);
  const page = loadedPage?.key === key ? loadedPage.page : null;

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    const timer = window.setTimeout(() => {
      void getCatalog(search, axis, value, controller.signal).then((result) => setLoadedPage({ key, page: result }), () => {
        if (!controller.signal.aborted) setError(true);
      });
    }, search ? 180 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search, axis, value, retry, key]);

  const stateOf = (song: CatalogSong): CardState => {
    if (song.stage_token) return { kind: "ready" };
    const id = youtubeId(song.source_url);
    const job = id ? jobs.find((row) => statusMeta(row.status).active && youtubeId(row.source_url.split("#")[0]) === id) ?? null : null;
    if (job) return { kind: "preparing", job, pct: Math.round(job.progress) };
    return pending.has(song.id) ? { kind: "preparing", job: null, pct: 0 } : { kind: "prepare" };
  };

  const doPrepare = (song: CatalogSong) => {
    if (pending.has(song.id)) return;
    setPending((ids) => new Set(ids).add(song.id));
    setPrepareError(false);
    prepare(song.source_url).catch(() => {
      setPrepareError(true);
      setPending((ids) => {
        const next = new Set(ids);
        next.delete(song.id);
        return next;
      });
    });
  };

  const card = (song: CatalogSong, versions: boolean) => {
    const state = stateOf(song);
    return (
      <SongLink key={song.id} song={song} state={state} onPrepare={doPrepare} className="sn-songcard">
        <Art song={song} ready={state.kind === "ready"} />
        <bdi className="sn-t1 sn-clamp2">{song.title}</bdi>
        <bdi className="sn-t2">{versions ? `${song.artist} · ${song.version}` : song.artist}</bdi>
        <StateLabel state={state} />
      </SongLink>
    );
  };

  // The recording version shows on a card only when one shelf holds two
  // recordings of the same song (#277); search results always show it.
  const duplicated = (songs: CatalogSong[]) => {
    const seen = new Map<string, number>();
    for (const song of songs) {
      const key = `${song.title}\u0000${song.artist}`.toLowerCase();
      seen.set(key, (seen.get(key) ?? 0) + 1);
    }
    return (song: CatalogSong) => (seen.get(`${song.title}\u0000${song.artist}`.toLowerCase()) ?? 0) > 1;
  };

  return (
    <div className="catalog-browser" aria-label={t("tab.catalog")}>
      {prepareError && <p className="sn-note is-err" role="alert"><Icon name="alert" size={20} />{t("err.prepare")}</p>}
      {error ? (
        <div>
          <p className="sn-note is-err" role="alert"><Icon name="alert" size={20} />{t("catalog.error")}</p>
          <button type="button" className="sn-btn sn-block sn-gap-top" onClick={() => setRetry((n) => n + 1)}>
            <Icon name="retry" size={20} />{t("action.retry")}
          </button>
        </div>
      ) : !page ? (
        <div className="catalog-skeleton" role="status" aria-label={t("catalog.loading")}>
          {[0, 1].map((i) => (
            <div key={i}>
              <div className="sn-shelf-head"><span className="sn-skel sn-skel-l1" /></div>
              <div className="sn-skel-cards">{[0, 1, 2].map((j) => <span key={j} className="sn-skel sn-skel-card" />)}</div>
            </div>
          ))}
        </div>
      ) : !page.songs.length ? (
        <div className="catalog-empty sn-empty sn-empty-compact">
          <Icon name="search" size={32} />
          <h2 className="sn-empty-title">{search ? t("catalog.noResults") : t("catalog.empty")}</h2>
          {search && <p className="sn-lead">{t("catalog.noResultsLead")}</p>}
          <button type="button" className="sn-btn" onClick={onAdd}><Icon name="plus" size={20} />{t("action.add")}</button>
        </div>
      ) : search ? (
        <section className="catalog-results">
          <p className="sn-hint sn-left" role="status">
            {page.songs.length === 1 ? t("catalog.countOne") : t("catalog.count", { n: page.songs.length })}
          </p>
          <ul className="sn-list">
            {page.songs.map((song) => {
              const state = stateOf(song);
              return (
                <li key={song.id}>
                  <SongLink song={song} state={state} onPrepare={doPrepare} className="sn-row">
                    <span className="sn-cover">
                      {song.thumbnail_url ? <img src={song.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <Icon name="note" />}
                    </span>
                    <span className="sn-grow">
                      <bdi className="sn-t1">{song.title}</bdi>
                      <bdi className="sn-t2">{song.artist} · {song.version}</bdi>
                    </span>
                    <StateLabel state={state} />
                  </SongLink>
                </li>
              );
            })}
          </ul>
        </section>
      ) : shelf ? (
        <section>
          <div className="sn-shelf-head">
            <h2>{shelfTitle(shelf)}</h2>
            <a className="sn-see-all" href={catalogHash()}>{t("catalog.allShelves")}</a>
          </div>
          <div className="catalog-grid">{(() => {
            const dup = duplicated(page.songs);
            return page.songs.map((song) => card(song, dup(song)));
          })()}</div>
        </section>
      ) : (
        [
          ...[...new Set([...PRIMARY, ...Object.keys(page.facets.collection)])]
            .map((value): CatalogShelf => ({ axis: "collection", value })),
          ...(["genre", "decade"] as const).flatMap((axis) => Object.entries(page.facets[axis])
            .filter(([, count]) => count >= 4)
            .map(([value]): CatalogShelf => ({ axis, value }))),
        ].map((group) => {
          const songs = page.songs.filter((song) => ({
            collection: song.collections, genre: song.genres, decade: [String(song.decade)],
          })[group.axis].includes(group.value));
          if (!songs.length) return null;
          const title = shelfTitle(group);
          const dup = duplicated(songs);
          return (
            <section key={group.axis + group.value} className="catalog-shelf" aria-label={title}>
              <div className="sn-shelf-head">
                <h2>{title}</h2>
                <a className="sn-see-all" href={catalogHash(group)} aria-label={t("catalog.seeAllLabel", { shelf: title })}>
                  {t("catalog.seeAll")}<Icon name="right" size={18} />
                </a>
              </div>
              <div className="catalog-shelf-scroll">{songs.slice(0, 8).map((song) => card(song, dup(song)))}</div>
            </section>
          );
        })
      )}
    </div>
  );
}

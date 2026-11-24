import { useEffect, useState } from "react";
import { type CatalogPage, type CatalogSong, getCatalog } from "../api";
import { t } from "../i18n";
import { catalogHash, type CatalogShelf, itemHash } from "../router";

const PRIMARY = ["Hebrew hits", "English hits"];
const GENRE_NAMES: Record<string, string> = { pop: "Pop", "hip-hop": "Hip hop", mizrahi: "Mizrahi" };

function shelfTitle(shelf: CatalogShelf) {
  if (shelf.axis === "decade") return shelf.value + "s";
  return shelf.axis === "genre" ? GENRE_NAMES[shelf.value] ?? shelf.value : shelf.value;
}

function SongCard({ song, preparing, prepare }: {
  song: CatalogSong;
  preparing: boolean;
  prepare: (song: CatalogSong) => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const label = song.stage_token ? "Sing" : "Prepare";
  const content = (
    <>
      <span className="catalog-art">
        {song.thumbnail_url && !imageFailed
          ? <img src={song.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} />
          : <span aria-hidden="true">♫</span>}
      </span>
      <strong dir="auto">{song.title}</strong>
      <span className="catalog-artist" dir="auto">{song.artist}</span>
      <span className="catalog-card-meta">
        <span>{song.version}</span>
        <span className={song.stage_token ? "catalog-ready" : undefined}>{song.stage_token ? "Готово" : "Доступна подготовка"}</span>
      </span>
      <span className={`m-btn sm ${song.stage_token ? "primary" : ""}`}>{preparing ? "Preparing…" : label}</span>
    </>
  );
  return song.stage_token
    ? <a className="catalog-card" data-catalog-song={song.id} href={itemHash(song.stage_token)}>{content}</a>
    : <button className="catalog-card" data-catalog-song={song.id} type="button" onClick={() => prepare(song)} disabled={preparing}>{content}</button>;
}

export function CatalogBrowser({ query, shelf, prepare, pasteUrl }: {
  query: string;
  shelf: CatalogShelf | null;
  prepare: (url: string) => Promise<void>;
  pasteUrl: () => void;
}) {
  const [page, setPage] = useState<CatalogPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [preparing, setPreparing] = useState<number | null>(null);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const search = query.trim();
  const axis = search ? undefined : shelf?.axis;
  const value = search ? undefined : shelf?.value;

  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    setError(null);
    const timer = window.setTimeout(() => {
      void getCatalog(search, axis, value, controller.signal).then(setPage, () => {
        if (!controller.signal.aborted) setError("Каталог не загрузился.");
      });
    }, search ? 180 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search, axis, value, retry]);

  const doPrepare = (song: CatalogSong) => {
    if (preparing !== null) return;
    setPreparing(song.id);
    setPrepareError(null);
    void prepare(song.source_url).catch(() => setPrepareError("Не удалось подготовить песню. Попробуйте ещё раз."))
      .finally(() => setPreparing(null));
  };

  const card = (song: CatalogSong) => <SongCard key={song.id} song={song} preparing={preparing === song.id} prepare={doPrepare} />;

  return (
    <div className="catalog-browser" aria-label="Каталог песен">
      {prepareError && <p role="alert">{prepareError}</p>}
      {error ? (
        <div role="alert">{error} <button type="button" className="m-btn sm" onClick={() => setRetry((n) => n + 1)}>Повторить</button></div>
      ) : !page ? (
        <div className="catalog-skeleton" role="status" aria-label="Загрузка каталога">
          {[0, 1, 2].map((i) => <span key={i} className="skel" />)}
        </div>
      ) : !page.songs.length ? (
        <div className="catalog-empty">
          <p>{search ? "Песни не найдены" : "Здесь пока нет песен"}</p>
          <button type="button" className="m-btn sm" onClick={pasteUrl}>{t("action.add")}</button>
        </div>
      ) : search || shelf ? (
        <section>
          {shelf && !search && (
            <div className="catalog-shelf-heading">
              <h2>{shelfTitle(shelf)}</h2>
              <a className="m-btn sm ghost" href={catalogHash()}>← Catalog</a>
            </div>
          )}
          <div className="catalog-grid">{page.songs.map(card)}</div>
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
          return (
            <section key={group.axis + group.value} className="catalog-shelf" aria-label={title}>
              <div className="catalog-shelf-heading">
                <h2>{title}</h2>
                <a className="m-btn sm ghost" href={catalogHash(group)} aria-label={`See all songs in ${title}`}>See all →</a>
              </div>
              <div className="catalog-shelf-scroll">{songs.slice(0, 8).map(card)}</div>
            </section>
          );
        })
      )}
    </div>
  );
}

// Sonereli UI/UX v1 shell: three tabs (My songs · Catalog · Settings) with the
// lime Add song action pinned above the tab bar on My songs and Catalog, in
// every state (empty library and load errors included). Stage and Add song are
// full-screen routes outside the shell.

import type { ReactNode } from "react";
import { t } from "../i18n";
import { addHash, catalogHash, mySongsHash, settingsHash } from "../router";
import { Icon, type IconName } from "./Icon";
import { MiniPlayer } from "./MiniPlayer";

export type ShellTab = "my-songs" | "catalog" | "settings";

const TABS: { key: ShellTab; href: string; label: () => string; icon: IconName }[] = [
  { key: "my-songs", href: mySongsHash, label: () => t("tab.mySongs"), icon: "songs" },
  { key: "catalog", href: catalogHash(), label: () => t("tab.catalog"), icon: "catalog" },
  { key: "settings", href: settingsHash(), label: () => t("tab.settings"), icon: "settings" },
];

export function MicMark({ size = 30 }: { size?: number }) {
  return <span className="sn-mark" aria-hidden="true"><Icon name="mic" size={size} /></span>;
}

export function Shell({ tab, title, actions, catalogEnabled, children }: {
  tab: ShellTab;
  title: string;
  /** Title-bar controls on the trailing edge (account, feedback). */
  actions?: ReactNode;
  /** The Catalog tab needs a Clerk session; the LAN booth hides it. */
  catalogEnabled: boolean;
  children: ReactNode;
}) {
  const showAdd = tab !== "settings";
  return (
    <div className="sn-app">
      <header className="sn-titlebar">
        <h1 className="sn-display sn-title">
          {tab === "my-songs" && <MicMark />}
          {title}
        </h1>
        <span className="sn-grow" />
        {actions}
      </header>
      <main className="sn-screen">{children}</main>
      <div className="sn-dock">
        <MiniPlayer />
        {showAdd && (
          <div className="sn-addbar">
            <a className="sn-btn sn-btn-primary sn-block sn-add-song" href={addHash}>
              <Icon name="plus" size={22} />
              {t("action.add")}
            </a>
          </div>
        )}
        <nav className="sn-tabbar" aria-label={t("tab.nav")}>
          {TABS.filter((item) => item.key !== "catalog" || catalogEnabled).map((item) => (
            <a key={item.key} className="sn-tab" href={item.href} aria-current={item.key === tab ? "page" : undefined}>
              <Icon name={item.icon} />
              <span>{item.label()}</span>
            </a>
          ))}
        </nav>
      </div>
    </div>
  );
}

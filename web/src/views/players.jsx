import { useEffect, useMemo, useState } from "react";
import { createRoleValuation, sourceFvm } from "../player-valuation.js";
import {
  Empty,
  PlayerRow,
  RoleChip,
  ROLE_LABELS,
  Segmented,
  Sheet,
  formatTier,
  useMediaQuery,
} from "../ui.jsx";

const PAGE = 60;

const ROLE_OPTIONS = [
  { value: "TUTTI", label: "Tutti" },
  ...Object.keys(ROLE_LABELS).map((role) => ({ value: role, label: role })),
];

const HISTORY_COLUMNS = [
  ["Pv", "PV"],
  ["Mv", "MV"],
  ["Fm", "FM"],
  ["Gf", "G"],
  ["Ass", "A"],
  ["Amm", "AMM"],
];

function MatchdayChart({ player }) {
  const voti = player.voto_puro_mean_per_giornata;
  const bonus = player.bonus_atteso_per_giornata;
  const pGioca = player.p_gioca_per_giornata;
  const [hovered, setHovered] = useState(null);
  if (!voti?.length) return null;
  const fv = voti.map((v, i) => v + (bonus?.[i] ?? 0));
  const min = Math.min(...fv);
  const max = Math.max(...fv);
  const range = max - min || 1;
  const W = 280;
  const H = 56;
  const barW = W / fv.length;
  const tooltipLeft =
    hovered !== null
      ? Math.min(Math.max(((hovered + 0.5) * barW) / W * 100, 10), 90)
      : 0;
  return (
    <div className="matchday-chart">
      <span>FM prevista per giornata</span>
      <div className="matchday-chart-wrap" onMouseLeave={() => setHovered(null)}>
        {hovered !== null && (
          <div className="matchday-tooltip" style={{ left: `${tooltipLeft}%` }}>
            <b>G{hovered + 1}</b>
            <span>{fv[hovered].toFixed(2)}</span>
            <small>{Math.round((pGioca?.[hovered] ?? 0) * 100)}% prob. voto</small>
          </div>
        )}
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          {fv.map((v, i) => {
            const h = Math.max(3, ((v - min) / range) * (H - 10) + 4);
            const opacity = 0.25 + (pGioca?.[i] ?? 0.5) * 0.75;
            return (
              <rect
                key={i}
                x={i * barW + 0.5}
                y={H - h}
                width={barW - 1}
                height={h}
                fill={hovered === i ? "var(--color-fanta-brand)" : "var(--color-fanta-go)"}
                opacity={hovered === i ? 1 : opacity}
                onMouseEnter={() => setHovered(i)}
              />
            );
          })}
        </svg>
      </div>
      <div className="chart-legend">
        <span>min {min.toFixed(2)}</span>
        <span>max {max.toFixed(2)}</span>
      </div>
    </div>
  );
}

/**
 * Player database. The list is the screen on phones and the detail arrives as a
 * sheet; from 1000px the detail becomes a sticky companion panel. Both render
 * the same PlayerDetail, so there is one description of a player in the app.
 */
export default function PlayersView({
  data,
  rules,
  selected,
  setSelected,
  initialRole,
  favorites = {},
  toggleFavorite,
  setFavoriteNote,
}) {
  const [query, setQuery] = useState("");
  const [role, setRole] = useState(initialRole || "TUTTI");
  const [team, setTeam] = useState("TUTTE");
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [sheetOpen, setSheetOpen] = useState(false);
  const isDesktop = useMediaQuery("(min-width: 1000px)");

  useEffect(() => {
    if (initialRole) setRole(initialRole);
  }, [initialRole]);

  useEffect(() => {
    if (selected && !isDesktop) setSheetOpen(true);
  }, [selected, isDesktop]);

  const valuation = useMemo(
    () => createRoleValuation(data.players, rules),
    [data.players, rules],
  );

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return data.players
      .filter(
        (player) =>
          (role === "TUTTI" || player.ruolo === role) &&
          (team === "TUTTE" || player.squadra === team) &&
          player.nome.toLowerCase().includes(needle) &&
          (!onlyFavorites || favorites[player.id]),
      )
      .sort((a, b) => valuation.normalizedFvm(b) - valuation.normalizedFvm(a));
  }, [data.players, query, role, team, valuation, favorites, onlyFavorites]);

  useEffect(() => setLimit(PAGE), [query, role, team]);

  const player = selected || rows[0];

  const pick = (next) => {
    setSelected(next);
    if (!isDesktop) setSheetOpen(true);
  };

  return (
    <>
      <div className="page-head">
        <span className="kicker">Listone</span>
        <h1>Profili, storico e proiezioni</h1>
      </div>

      <div className="filters">
        <div className="filters-row">
          <input
            className="input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Cerca un giocatore"
            type="search"
            aria-label="Cerca un giocatore"
          />
          <select
            className="select"
            value={team}
            onChange={(event) => setTeam(event.target.value)}
            aria-label="Filtra per squadra"
            style={{ maxWidth: "9.5rem" }}
          >
            <option value="TUTTE">Tutte</option>
            {data.teams.map((item) => (
              <option key={item.squadra}>{item.squadra}</option>
            ))}
          </select>
        </div>
        <div className="filters-row">
          <Segmented
            options={ROLE_OPTIONS}
            value={role}
            onChange={setRole}
            label="Filtra per ruolo"
            roleColors
          />
          <span className="filters-count">{rows.length}</span>
        </div>
        {toggleFavorite ? (
          <div className="filters-row">
            <button
              type="button"
              className={`btn btn--sm ${onlyFavorites ? "btn--primary" : ""}`}
              onClick={() => setOnlyFavorites((v) => !v)}
              aria-pressed={onlyFavorites}
            >
              ★ {onlyFavorites ? "Solo preferiti" : "Preferiti"} {Object.keys(favorites).length ? `(${Object.keys(favorites).length})` : ""}
            </button>
          </div>
        ) : null}
      </div>

      <div className="players-split">
        <section className="card card--flush">
          {rows.length ? (
            <>
              {/* One column header replaces the per-row unit label that used to
                  repeat five hundred times down the list. */}
              <div className="list-head">
                <span>Giocatore</span>
                <span>Valore ruolo</span>
              </div>
              <div className="rows">
                {rows.slice(0, limit).map((item) => (
                  <div key={item.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <PlayerRow
                        player={item}
                        className="player-row"
                        selected={isDesktop && player?.id === item.id}
                        value={valuation.normalizedFvm(item).toFixed(1)}
                        onClick={() => pick(item)}
                      />
                    </div>
                    {toggleFavorite ? (
                      <button
                        type="button"
                        className={`fav-btn ${favorites[item.id] ? "active" : ""}`}
                        onClick={(e) => { e.stopPropagation(); toggleFavorite(item.id); }}
                        aria-label={favorites[item.id] ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti"}
                        title={favorites[item.id] ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti"}
                      >
                        {favorites[item.id] ? "★" : "☆"}
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              {rows.length > limit ? (
                <div style={{ padding: "var(--s-3)" }}>
                  <button
                    type="button"
                    className="btn btn--block"
                    onClick={() => setLimit((value) => value + PAGE)}
                  >
                    Mostra altri {Math.min(PAGE, rows.length - limit)}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <Empty title="Nessun giocatore trovato">
              Prova a cambiare ruolo, squadra o testo cercato.
            </Empty>
          )}
        </section>

        {isDesktop && player ? (
          <aside className="player-detail-panel">
            <div className="card">
              <PlayerDetail
                player={player}
                valuation={valuation}
                favorite={favorites[player.id]}
                onToggleFavorite={toggleFavorite ? () => toggleFavorite(player.id) : undefined}
                onSetNote={setFavoriteNote ? (note) => setFavoriteNote(player.id, note) : undefined}
              />
            </div>
          </aside>
        ) : null}
      </div>

      {!isDesktop ? (
        <Sheet
          open={sheetOpen && Boolean(player)}
          onClose={() => setSheetOpen(false)}
          title="Scheda giocatore"
        >
          {player ? (
            <PlayerDetail
              player={player}
              valuation={valuation}
              favorite={favorites[player.id]}
              onToggleFavorite={toggleFavorite ? () => toggleFavorite(player.id) : undefined}
              onSetNote={setFavoriteNote ? (note) => setFavoriteNote(player.id, note) : undefined}
            />
          ) : null}
        </Sheet>
      ) : null}
    </>
  );
}

/** The single description of a player: figures, quotations, history, status. */
export function PlayerDetail({ player, valuation, favorite, onToggleFavorite, onSetNote }) {
  const history = Object.entries(player.storico || {});
  const outliers = valuation.outliersFor(player);
  const difference = player.quotazioni.differenza;

  return (
    <div className="stack">
      <div className="detail-head">
        <RoleChip role={player.ruolo} large />
        <div style={{ minWidth: 0, flex: 1 }}>
          <h2>{player.nome}</h2>
          <p>
            {player.squadra} · Mantra {player.ruoli_mantra || "n/d"}
          </p>
        </div>
        <span className="pill pill--brand">
          {formatTier(player.guida_asta_fascia)}
        </span>
        {onToggleFavorite ? (
          <button
            type="button"
            className={`fav-btn ${favorite ? "active" : ""}`}
            onClick={() => onToggleFavorite()}
            aria-label={favorite ? "Rimuovi preferito" : "Aggiungi preferito"}
            title={favorite ? "Rimuovi dai preferiti" : "Aggiungi ai preferiti"}
            style={{ fontSize: 28, marginLeft: 8 }}
          >
            {favorite ? "★" : "☆"}
          </button>
        ) : null}
      </div>
      {onSetNote ? (
        <div className="fav-note">
          <b>Nota {favorite ? "preferito" : "giocatore"}</b>
          <textarea
            value={favorite?.note || ""}
            onChange={(e) => onSetNote?.(e.target.value)}
            placeholder={favorite ? "Aggiungi una nota..." : "Aggiungi una nota (crea preferito)..."}
            rows={2}
          />
          {!favorite ? <small className="micro">Salvando la nota il giocatore diventa preferito.</small> : null}
        </div>
      ) : null}

      <div className="detail-figures">
        <div className="stat">
          <span className="stat-label">FVM fonte</span>
          <span className="stat-value">{sourceFvm(player).toFixed(2)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Valore ruolo</span>
          <span className="stat-value">
            {valuation.normalizedFvm(player).toFixed(2)}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Prob. voto</span>
          <span className="stat-value">
            {Math.round(player.proiezione.p_gioca * 100)}%
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Fantavoto</span>
          <span className="stat-value">
            {player.proiezione.fantavoto.toFixed(2)}
          </span>
        </div>
      </div>

      {outliers.length ? (
        <div className="notice notice--warn" role="note">
          <b>Valore da verificare</b>
          <ul className="bullets bullets--warn" style={{ marginTop: "var(--s-2)" }}>
            {outliers.map((outlier) => (
              <li key={outlier.code}>{outlier.label}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="quotes">
        <span className="muted">
          Quotazione <b>{player.quotazioni.attuale}</b>
        </span>
        <span className="muted">
          Iniziale <b>{player.quotazioni.iniziale}</b>
        </span>
        <span className={difference >= 0 ? "trend-up" : "trend-down"}>
          {difference >= 0 ? "+" : ""}
          {difference}
        </span>
      </div>

      {player.prezzi_asta ? (
        <div className="prezzi-grid">
          <div>
            <span>8 squadre / 350</span>
            <b>{player.prezzi_asta["8_350"] ?? "—"}</b>
          </div>
          <div>
            <span>10 squadre / 350</span>
            <b>{player.prezzi_asta["10_350"] ?? "—"}</b>
          </div>
          <div>
            <span>8 squadre / 500</span>
            <b>{player.prezzi_asta["8_500"] ?? "—"}</b>
          </div>
          <div>
            <span>10 squadre / 500</span>
            <b>{player.prezzi_asta["10_500"] ?? "—"}</b>
          </div>
        </div>
      ) : null}

      <MatchdayChart player={player} />

      <div>
        <div className="section-head">
          <h2 style={{ fontSize: "var(--fs-md)" }}>Storico</h2>
        </div>
        {history.length ? (
          <table className="history-table">
            <thead>
              <tr>
                <th>Stagione</th>
                {HISTORY_COLUMNS.map(([, label]) => (
                  <th key={label}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.map(([season, stat]) => (
                <tr key={season}>
                  <td>{season}</td>
                  {HISTORY_COLUMNS.map(([key]) => (
                    <td key={key}>{stat[key] ?? "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="micro">Nessuno storico nel listone.</p>
        )}
      </div>

      <div className="notice">
        <b>{player.disponibilita.status.replace("_", " ")}</b>
        <p style={{ marginTop: 4 }}>
          {player.disponibilita.nota || "Stima ricavata dallo storico."}
        </p>
      </div>

      <p className="micro">
        FVM fonte: colonna FVM del listone Fantacalcio su base 1000. Il valore
        ruolo la normalizza sul budget configurato per il reparto.
      </p>
    </div>
  );
}

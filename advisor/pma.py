"""PMA 2026/27 — sorgenti verificate e pipeline di normalizzazione.

Sorgenti (verificate 27/08/2026):

- Fantacalcio-Online (VERIFICATO, MIGLIORE): https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi
  HTML tabella id=players_list, 679 giocatori, 295 con prezzo 2026/27 puro (al 26/08),
  4 colonne: 8/350, 10/350, 8/500, 10/500. PMA reale = media pagato reale
  (50k+ acquisti, soglia >=3 aste). Gratuito, scraping stabile, no API REST.
  Fallback legacy: https://leghe.fantacalcio-online.com/... (vuoto oggi).

- Economia e Sport (VERIFICATO, SECONDARIO): https://www.economiaesport.it/2026/08/14/fantacalcio-2026-2027-prezzi-medi-asta-giocatori-piu-pagati-sottovalutati-e-hype/
  Tabella 504 giocatori, budget 500 fisso, colonna PMeA% + PMeA(500).
  Sorgente: "decine di leghe", meno granulare (no partecipanti). Gratuito.

- FantaMaster Tool (VERIFICATO, NON ACCESSIBILE): https://leghe.fantamaster.it/tool/players/
  Reflex WebSocket wss://leghe.fantamaster.it/_event, stato pl_prices_rx_state_[player][8].avg.
  Richiede signup + Premium (has_tool_rx_state, has_premium_rx_state gate con lucchetto).
  Simulatore rosa: "Funzionalita' riservata agli utenti Premium".
  Nessun endpoint REST replicabile con requests.get/post senza sessione autenticata.
  -> Documentato come NON ACCESSIBILE, non bypassato.

- Fantaculo PRO, FantaLab, Fantamagazine: paywall o non-PMA (vedi report).

Output atteso: pma_2026_27.csv con almeno:
  season,player_id,player_name,team,role,pma,budget,participants,source
Normalizzazione nomi coerente con advisor.pipeline.normalize.
"""
from __future__ import annotations

import csv
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FCO_URL = "https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi"
FCO_FALLBACK_URL = "https://leghe.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi"
ECONOMIA_URL = "https://www.economiaesport.it/2026/08/14/fantacalcio-2026-2027-prezzi-medi-asta-giocatori-piu-pagati-sottovalutati-e-hype/"

CACHE_HOURS = 6
OUTPUT_FILENAME = "pma_2026_27.csv"
SEASON = "2026/27"


def normalize_name(value: object) -> str:
    """Coerente con pipeline.normalize: lower, NFD, ascii, keep hyphen."""
    value = unicodedata.normalize("NFKD", str(value).lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    return " ".join(value.split())


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600
    return age < CACHE_HOURS


def fetch_fco_raw(*, timeout: int = 30) -> pd.DataFrame:
    """Scrape tabella players_list da fantacalcio-online (4 prezzi)."""
    # Reuse logic from scrape_prezzi_asta but also expose quotazione
    last_exc: Exception | None = None
    for url in (FCO_URL, FCO_FALLBACK_URL):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            r.raise_for_status()
            try:
                soup = BeautifulSoup(r.text, "lxml")
            except Exception:
                soup = BeautifulSoup(r.text, "html.parser")
            table = soup.find("table", {"id": "players_list"})
            if table is None:
                raise ValueError(f"players_list not found at {url}")
            df = _parse_fco_table(table, url)
            priced = sum(df[c].notna().sum() for c in ["prezzo_8_350", "prezzo_10_350", "prezzo_8_500", "prezzo_10_500"])
            if priced > 50:
                return df
            last_exc = ValueError(f"only {priced} priced rows at {url}")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("FCO fetch failed %s: %s", url, exc)
    raise last_exc or RuntimeError("FCO fetch failed")


def _parse_fco_table(table, url: str) -> pd.DataFrame:  # noqa: ANN001
    thead = table.find("thead")
    headers = []
    if thead:
        headers = [th.get_text(" ", strip=True).lower() for th in thead.find_all("th")]

    def find_idx(pred):
        for i, h in enumerate(headers):
            if pred(h):
                return i
        return None

    if not headers:
        idx_role, idx_team, idx_name, idx_qta = 0, 1, 2, 3
        idx_8_350, idx_10_350, idx_8_500, idx_10_500 = 4, 5, 6, 7
    else:
        idx_role = find_idx(lambda h: "ruolo" in h or h == "rt")
        idx_team = find_idx(lambda h: "squadra" in h)
        idx_name = find_idx(lambda h: "nome" in h)
        idx_qta = find_idx(lambda h: "kap" in h or "m.m" in h or "quot" in h)
        idx_8_350 = find_idx(lambda h: "350" in h and "8" in h)
        idx_10_350 = find_idx(lambda h: "350" in h and "10" in h)
        idx_8_500 = find_idx(lambda h: "500" in h and "8" in h)
        idx_10_500 = find_idx(lambda h: "500" in h and "10" in h)
        if None in (idx_role, idx_team, idx_name, idx_8_350):
            has_ctrl = len(headers) == 11 and headers[0] == ""
            if has_ctrl:
                idx_role, idx_team, idx_name, idx_qta = 1, 2, 3, 4
                idx_8_350, idx_10_350, idx_8_500, idx_10_500 = 5, 6, 7, 8
            else:
                idx_role, idx_team, idx_name, idx_qta = 0, 1, 2, 3
                idx_8_350, idx_10_350, idx_8_500, idx_10_500 = 4, 5, 6, 7

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        need = max(idx_8_350, idx_10_350, idx_8_500, idx_10_500)
        if len(tds) <= need:
            continue
        role = tds[idx_role].get_text(strip=True) if idx_role is not None else ""
        if not role:
            sp = tds[idx_role].find("span") if idx_role is not None else None
            if sp:
                role = sp.get_text(strip=True)
        team = tds[idx_team].get_text(strip=True) if idx_team is not None else ""
        name_td = tds[idx_name] if idx_name is not None else None
        name = _extract_fco_name(name_td)
        qta = ""
        if idx_qta is not None and idx_qta < len(tds):
            qta = tds[idx_qta].get_text(strip=True)

        def price(idx):
            v = tds[idx].get_text(strip=True) if idx < len(tds) else ""
            if not v or v == "-":
                return None
            try:
                return float(v.replace(",", "."))
            except ValueError:
                return None

        rows.append({
            "ruolo": role,
            "squadra": team,
            "nome": name,
            "quotazione": qta,
            "prezzo_8_350": price(idx_8_350),
            "prezzo_10_350": price(idx_10_350),
            "prezzo_8_500": price(idx_8_500),
            "prezzo_10_500": price(idx_10_500),
            "source_url": url,
        })
    return pd.DataFrame(rows)


def _extract_fco_name(td) -> str:  # noqa: ANN001
    if td is None:
        return ""
    bold = td.find(class_="text-bold")
    muted = td.find(class_="text-muted")
    hidden = td.find(class_="hidden-xl-down")
    spans = td.find_all("span")
    if bold:
        surname = bold.get_text(strip=True)
        firstname = ""
        if muted:
            firstname = muted.get_text(strip=True)
        elif hidden:
            firstname = hidden.get_text(strip=True)
        elif len(spans) >= 2:
            for sp in spans:
                if sp is not bold:
                    txt = sp.get_text(strip=True)
                    if txt:
                        firstname = txt
                        break
        if surname or firstname:
            return f"{surname} {firstname}".strip()
    raw = td.get_text(" ", strip=True)
    if raw and " " not in raw:
        fixed = re.sub(r"([A-ZÀ-Ý]{2,})([A-Z][a-zàèéìòù])", r"\1 \2", raw)
        if " " in fixed:
            raw = fixed
        else:
            m = re.match(r"^([A-ZÀ-Ý'\-]+)([A-Z][a-zàèéìòù].*)", raw)
            if m:
                raw = f"{m.group(1)} {m.group(2)}"
    return raw.strip()


def fetch_economia_raw(*, timeout: int = 30) -> pd.DataFrame:
    """Scrape Economia e Sport tabella PMeA (budget 500)."""
    r = requests.get(ECONOMIA_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("No tables found at Economia e Sport")
    # first table is main, second is Mercato snippet
    table = tables[0]
    rows = table.find_all("tr")
    if not rows:
        raise ValueError("Empty table at Economia e Sport")
    # header: R, RM, Nome, Squadra, Qt.A, PMeA%, PMeA (500)
    data = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        vals = [c.get_text(strip=True) for c in tds]
        # vals: R, RM, Nome, Squadra, Qt.A, PMeA%, PMeA(500)
        # some rows have 7 cols, handle
        R, RM, Nome, Squadra, QtA, PMeA_pct, PMeA500 = vals[:7]
        # skip completely empty
        if not Nome:
            continue
        data.append({
            "ruolo": R,
            "ruolo_mantra": RM,
            "nome": Nome,
            "squadra": Squadra,
            "quotazione": QtA,
            "pmea_percent": PMeA_pct,
            "pmea_500": PMeA500,
        })
    return pd.DataFrame(data)


def build_pma_dataset(raw_dir: Path | str = "data/raw", *, force: bool = False, output_path: Path | str | None = None) -> pd.DataFrame:
    """Costruisce dataset normalizzato PMA 2026/27 unendo FCO + Economia.

    Output schema (richiesto):
      season, player_id, player_name, team, role, pma, budget, participants, source
    + extra utili: pma_percent, quotazione, normalized_name

    Salva su raw_dir/pma_2026_27.csv di default, con cache 6h se non force.
    """
    raw_dir = Path(raw_dir)
    output_path = Path(output_path) if output_path else raw_dir / OUTPUT_FILENAME

    if not force and _is_fresh(output_path):
        try:
            return pd.read_csv(output_path)
        except Exception:
            pass

    # 1. FCO
    fco_df = fetch_fco_raw()
    # 2. Economia
    try:
        econ_df = fetch_economia_raw()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Economia fetch failed: %s", exc)
        econ_df = pd.DataFrame()

    # Normalize long format: one row per budget/participants
    rows = []
    pid = 0
    for _, r in fco_df.iterrows():
        pid += 1
        base = {
            "season": SEASON,
            "player_id": pid,
            "player_name": r["nome"],
            "team": r["squadra"],
            "role": r["ruolo"],
            "quotazione": r["quotazione"],
            "normalized_name": normalize_name(r["nome"]),
            "source": "fantacalcio-online.com/asta-fantacalcio-stima-prezzi",
        }
        for budget, part, col in [(350, 8, "prezzo_8_350"), (350, 10, "prezzo_10_350"), (500, 8, "prezzo_8_500"), (500, 10, "prezzo_10_500")]:
            val = r[col]
            if pd.isna(val):
                continue
            pma = float(val)
            pct = round(pma / budget * 100, 2)
            rows.append({**base, "pma": pma, "pma_percent": pct, "budget": budget, "participants": part})

    # Economia: budget 500, participants unknown -> 0 (media classic)
    eid = 10000
    for _, r in econ_df.iterrows():
        raw500 = str(r["pmea_500"]).strip()
        if not raw500 or raw500 == "0":
            continue
        try:
            pma = float(raw500.replace(",", "."))
        except ValueError:
            continue
        if pma == 0:
            continue
        try:
            pct_raw = str(r["pmea_percent"]).replace(",", ".")
            pct = float(pct_raw) if pct_raw else round(pma / 500 * 100, 2)
        except ValueError:
            pct = round(pma / 500 * 100, 2)
        eid += 1
        rows.append({
            "season": SEASON,
            "player_id": eid,
            "player_name": r["nome"],
            "team": r["squadra"],
            "role": r["ruolo"],
            "quotazione": r["quotazione"],
            "normalized_name": normalize_name(r["nome"]),
            "source": "economiaesport.it",
            "pma": pma,
            "pma_percent": pct,
            "budget": 500,
            "participants": 0,
        })

    out = pd.DataFrame(rows, columns=["season","player_id","player_name","team","role","pma","pma_percent","budget","participants","source","quotazione","normalized_name"])
    # Sort by pma desc for readability
    out = out.sort_values(["budget","participants","pma"], ascending=[True, True, False]).reset_index(drop=True)

    raw_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False, quoting=csv.QUOTE_MINIMAL)
    logger.info("PMA dataset: %d righe (%d FCO + %d Economia) -> %s", len(out), len(out[out.source.str.contains("fantacalcio")]), len(out[out.source.str.contains("economia")]), output_path)
    return out


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build PMA 2026/27 dataset")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=None, help="Output CSV path (default data/raw/pma_2026_27.csv)")
    parser.add_argument("--force", action="store_true", help="Ignore cache")
    parser.add_argument("--econ-only", action="store_true", help="Only test Economia")
    parser.add_argument("--fco-only", action="store_true", help="Only test FCO")
    args = parser.parse_args()

    if args.econ_only:
        df = fetch_economia_raw()
        print(f"Economia: {len(df)} righe")
        print(df.head(10).to_string())
        return 0
    if args.fco_only:
        df = fetch_fco_raw()
        print(f"FCO: {len(df)} righe, priced {sum(df[c].notna().sum() for c in ['prezzo_8_350','prezzo_10_350','prezzo_8_500','prezzo_10_500'])} total")
        print(df.head(10).to_string())
        return 0

    out = build_pma_dataset(args.raw_dir, force=args.force, output_path=args.output)
    print(f"Salvato {len(out)} righe -> {args.output or args.raw_dir / OUTPUT_FILENAME}")
    print(out.head(10).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

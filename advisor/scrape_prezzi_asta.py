"""Fetch and cache auction price estimates from fantacalcio-online.com.

Primary source: https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi
  (PMA reale, 4 combinazioni budget/partecipanti, aggiornato nightly, gratuito).

Fallback: https://leghe.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi
  (legacy domain, attualmente vuoto per molti giocatori).

Economia e Sport è gestito separatamente in advisor.pma (budget 500 fisso,
decine di leghe) e non è usato come prezzi_asta primari per non perdere
granularità budget/partecipanti.

FantaMaster / Fantaculo sono documentati come NON ACCESSIBILI senza Premium:
vedi advisor/pma.py per dettagli tecnici (Reflex WebSocket, paywall).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

URL = "https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi"
FALLBACK_URL = "https://leghe.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi"
URLS = [URL, FALLBACK_URL]
CACHE_HOURS = 6
OUTPUT_FILENAME = "prezzi_asta.csv"

logger = logging.getLogger(__name__)


def fetch_prezzi_asta(raw_dir: Path, *, force: bool = False) -> pd.DataFrame | None:
    """Return auction price estimates, fetching fresh data when the cache is stale."""
    cache_path = raw_dir / OUTPUT_FILENAME
    if not force and _is_fresh(cache_path):
        return pd.read_csv(cache_path)
    try:
        df = _scrape()
        raw_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
        logger.info("Fetched %d prezzi asta → %s", len(df), cache_path)
        return df
    except Exception as exc:
        logger.warning("Could not fetch prezzi asta: %s", exc)
        if cache_path.exists():
            return pd.read_csv(cache_path)
        return None


def _is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_hours = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600
    return age_hours < CACHE_HOURS


def _scrape() -> pd.DataFrame:
    import requests
    from bs4 import BeautifulSoup

    last_error: Exception | None = None
    for url in URLS:
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            response.raise_for_status()
            # html.parser is always available; lxml optional
            try:
                soup = BeautifulSoup(response.text, "lxml")
            except Exception:
                soup = BeautifulSoup(response.text, "html.parser")
            table = soup.find("table", {"id": "players_list"})
            if table is None:
                raise ValueError(f"players_list table not found at {url}")
            df = _parse_players_list(table)
            # www domain should have ~679 rows and >200 priced; leghe domain currently empty
            # prefer the first URL that yields priced rows
            priced = sum(df[col].notna().sum() for col in ["prezzo_8_350", "prezzo_10_350", "prezzo_8_500", "prezzo_10_500"])
            if priced > 50:
                logger.info("Scraped %d rows (%d priced) from %s", len(df), priced, url)
                return df
            logger.warning("Scraped %d rows but only %d priced from %s, trying fallback", len(df), priced, url)
            last_error = ValueError(f"insufficient priced rows from {url}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Failed to scrape %s: %s", url, exc)
    if last_error:
        raise last_error
    raise RuntimeError("No URL succeeded")


def _parse_players_list(table) -> pd.DataFrame:  # noqa: ANN001
    """Parse <table id=players_list> handling both www and leghe variants."""
    # Detect header indices dynamically: www -> Ruolo,Squadra,Nome,Kap,8/350,10/350,8/500,10/500,MV,Pres (10 cols)
    # leghe -> "",RT,Squadra,Nome,M.M.,350K (8),350K (10),500K (8),500K (10),MV,Pres (11 cols)
    thead = table.find("thead")
    headers = []
    if thead:
        headers = [th.get_text(" ", strip=True).lower() for th in thead.find_all("th")]

    def _find_idx(predicate) -> int | None:
        for i, h in enumerate(headers):
            if predicate(h):
                return i
        return None

    # fallback fixed mapping if headers empty
    if not headers:
        # assume www layout
        idx_role, idx_team, idx_name = 0, 1, 2
        idx_8_350, idx_10_350, idx_8_500, idx_10_500 = 4, 5, 6, 7
    else:
        # role: header contains "ruolo" or "rt"
        idx_role = _find_idx(lambda h: "ruolo" in h or h == "rt")
        idx_team = _find_idx(lambda h: "squadra" in h)
        idx_name = _find_idx(lambda h: "nome" in h)
        # price cols: detect by budget + participants in same header
        # www: "8 sq. / 350" -> contains both 8 and 350; leghe: "350k (8)" -> same
        idx_8_350 = _find_idx(lambda h: "350" in h and "8" in h)
        idx_10_350 = _find_idx(lambda h: "350" in h and "10" in h)
        idx_8_500 = _find_idx(lambda h: "500" in h and "8" in h)
        idx_10_500 = _find_idx(lambda h: "500" in h and "10" in h)
        # fallback to positional if detection fails
        if None in (idx_role, idx_team, idx_name, idx_8_350, idx_10_350, idx_8_500, idx_10_500):
            # counters include possible leading details-control col on leghe (empty header)
            has_control = len(headers) == 11 and headers[0] == ""
            offset = 1 if has_control else 0
            # www not has control
            if has_control:
                idx_role, idx_team, idx_name = 1, 2, 3
                idx_8_350, idx_10_350, idx_8_500, idx_10_500 = 5, 6, 7, 8
            else:
                idx_role, idx_team, idx_name = 0, 1, 2
                idx_8_350, idx_10_350, idx_8_500, idx_10_500 = 4, 5, 6, 7

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < max(idx_8_350, idx_10_350, idx_8_500, idx_10_500) + 1:
            continue

        role = tds[idx_role].get_text(strip=True) if idx_role is not None else ""
        # role tag may be single letter inside span
        if not role:
            span = tds[idx_role].find("span")
            if span:
                role = span.get_text(strip=True)
        team = tds[idx_team].get_text(strip=True) if idx_team is not None else ""
        name_td = tds[idx_name] if idx_name is not None else None
        name = _extract_name(name_td) if name_td is not None else ""

        def price(idx: int) -> float | None:
            v = tds[idx].get_text(strip=True) if idx < len(tds) else ""
            if not v or v == "-":
                return None
            try:
                return float(v.replace(",", "."))
            except (ValueError, AttributeError):
                return None

        rows.append({
            "ruolo": role,
            "squadra": team,
            "nome": name,
            "prezzo_8_350": price(idx_8_350),
            "prezzo_10_350": price(idx_10_350),
            "prezzo_8_500": price(idx_8_500),
            "prezzo_10_500": price(idx_10_500),
        })

    return pd.DataFrame(rows)


def _extract_name(td) -> str:  # noqa: ANN001
    """Robustly extract 'SURNAME First' from player-name cell.

    Handles:
    - www: <span class=text-bold>MALEN</span> <span class=text-muted>Donyell</span>
    - leghe: <span class=text-bold>RATERINK</span> <span class=hidden-xl-down>Othniël</span>
    - fallback: plain text with possible concatenation (MALENDonyell) -> insert space before caps
    """
    if td is None:
        return ""
    # try span-based
    bold = td.find(class_="text-bold")
    # second span can be text-muted or hidden-xl-down
    muted = td.find(class_="text-muted")
    hidden = td.find(class_="hidden-xl-down")
    # generic second span
    spans = td.find_all("span")
    if bold:
        surname = bold.get_text(strip=True)
        firstname = ""
        if muted:
            firstname = muted.get_text(strip=True)
        elif hidden:
            firstname = hidden.get_text(strip=True)
        elif len(spans) >= 2:
            # take second span that's not bold
            for sp in spans:
                if sp is not bold:
                    txt = sp.get_text(strip=True)
                    if txt:
                        firstname = txt
                        break
        if surname or firstname:
            return f"{surname} {firstname}".strip()
    # fallback: get_text with space separator and fix concatenation
    raw = td.get_text(" ", strip=True)
    # Fix case like "MALENDonyell" (no space between caps) by detecting lower->upper transition
    # Insert space before capital that follows lower or before capital-lower block after caps block
    if raw and " " not in raw:
        # e.g., MALENDonyell -> MALEN Donyell ; MARTINEZLautaro -> MARTINEZ Lautaro
        # heuristic: split where sequence of caps (2+) is followed by Cap+lower
        m = re.search(r"[a-zàèéìòù]", raw)
        if m:
            # find first lowercase; surname is caps prefix before that word's capital
            # simpler: find boundary where a capital letter is followed by lowercase and preceded by capital
            # Insert space before that capital
            fixed = re.sub(r"([A-ZÀ-Ý]{2,})([A-Z][a-zàèéìòù])", r"\1 \2", raw)
            if " " in fixed:
                raw = fixed
            else:
                # fallback: split at position where lower->upper transition reverse: MALEN|Donyell
                # find first occurrence of [a-z][A-Z] not applicable, so try caps+Capitalized pattern
                # Use regex to split caps sequence + Title word
                mm = re.match(r"^([A-ZÀ-Ý'\-]+)([A-Z][a-zàèéìòù].*)", raw)
                if mm:
                    raw = f"{mm.group(1)} {mm.group(2)}"
    # normalize spaces, title case will be done downstream if needed
    return raw.strip()

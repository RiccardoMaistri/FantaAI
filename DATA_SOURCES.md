# Data Sources

The project ships structured, versioned input files in `data/raw/` so a fresh
clone can generate a dataset locally. The files cover the player list, Serie A
calendar, historical statistics, club priors, likely starters, set-piece order,
and auction tiers.

The private fantasy-league calendar is deliberately not committed. It is
optional for generating dashboard, projection, and auction data, but required
to simulate a season. Upload it from **Impostazioni**, then regenerate the
dataset before simulation. Its teams and matchday count are checked against the
profile during generation.

## League calendar

Download the sanitized workbook model from **Impostazioni**. The importer reads
the legacy Leghe Fantacalcio layout, not an arbitrary spreadsheet:

- Use an `.xlsx` workbook with a worksheet named exactly `Calendario`.
- Keep the two fixture blocks in columns A:D and G:J. The other columns may be
  used for display or score values and are ignored.
- Begin each block with `Nª Giornata lega` in column A or G and `Mª Giornata
  serie a` in column C or I.
- Put each home team in A and away team in D for the left block; use G and J
  for the right block. Fixture rows continue until the next header.
- Blank rows are allowed. Each fixture needs two distinct teams, and a team
  cannot play twice in one matchday.

For example:

```text
A: 1ª Giornata lega       C: 1ª Giornata serie a
A: Squadra 1              D: Squadra 2
A: Squadra 3              D: Squadra 4

G: 2ª Giornata lega       I: 2ª Giornata serie a
G: Squadra 1              J: Squadra 3
G: Squadra 2              J: Squadra 4
```

Generation requires consecutive league matchdays starting at 1 and exactly the
configured number of fantasy matchdays. The team names must match the profile;
the API adopts valid uploaded calendar names as the profile participants when
the data is generated.

The application treats the files as input data, not as a remote scraping layer.
If you replace them for another season, update the profile source declarations
and retain attribution required by the data license.

## Prezzi Medi Asta (PMA) 2026/27

PMA reali (prezzi pagati, non quotazioni/FVM) sono integrati via scraping
verificato al 27/08/2026. Vedi `advisor/pma.py` per dettagli tecnici e
`advisor/scrape_prezzi_asta.py` per il codice di fetch/cache.

| Sorgente | URL | Tipo | PMA reale | Granularità | Auth | Stato |
|---|---|---|---|---|---|---|
| **Fantacalcio-Online** (primario) | `https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi` fallback `https://leghe.fantacalcio-online.com/...` | HTML `table#players_list` (679 giocatori, 1319 prezzi su 4 colonne) | **Sì** — media pagato su 50k+ acquisti, soglia ≥3 aste, 295 con prezzo 2026/27 puro al 26/08 | 350/500 crediti × 8/10 partecipanti (4 combo) + MV/Pres | **No** | **VERIFICATO** gratuito, nightly |
| Economia e Sport (secondario) | `https://www.economiaesport.it/2026/08/14/fantacalcio-2026-2027-prezzi-medi-asta-giocatori-piu-pagati-sottovalutati-e-hype/` | HTML `table border=3` (504 giocatori) | Sì ma decine di leghe | 500 crediti, classic, no partecipanti | No | VERIFICATO gratuito |
| FantaMaster Tool | `https://leghe.fantamaster.it/tool/players/` `…/squad-simulator` | Reflex WebSocket `wss://leghe.fantamaster.it/_event` stato `pl_prices_rx_state_[player][8].avg` | Sì (claim migliaia leghe, tempo reale) | % budget + 6/8/10/12+ + mod. difesa | **Sì** Premium (`has_tool`, `has_premium` gate, lock SVG) | **NON ACCESSIBILE** senza Premium — non bypassato |
| Fantaculo PRO | `https://www.fantaculo.it/pro/auction` | HTML + Excel export PRO | Sì (giornaliero) | Budget/partecipanti personalizzato | Sì PRO | NON ACCESSIBILE free |
| FantaLab / Fantamagazine Excel | `https://www.fantalab.it` / `https://store.fantamagazine.com` | Excel/SPA | No (FVM/quotazioni o fasce) | — | Sì (a pagamento) | Non-PMA |

**Output generato:**

- `data/raw/prezzi_asta.csv` — cache legacy per `advisor.pipeline` (ruolo,squadra,nome,4 prezzi). Rigenerato via `fetch_prezzi_asta()` con cache 6h.
- `data/raw/pma_2026_27.csv` — dataset normalizzato (season,player_id,player_name,team,role,pma,budget,participants,source + pma_percent,quotazione,normalized_name). Generato via `advisor.pma.build_pma_dataset()` (1622 righe: 1319 FCO + 303 Economia).

Rigenera:

```bash
.venv/bin/python -m advisor.pma --raw-dir data/raw --force        # pma_2026_27.csv
.venv/bin/python -m advisor.scrape_prezzi_asta  # via fetch_prezzi_asta(force=True)
.venv/bin/python -m advisor.pipeline --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed
```

I prezzi sono assoluti in crediti; per leghe con budget diverso da 350/500 usa `pma_percent = pma/budget*100` (es. Malen 134.68 su 500 → 26.94%).

## Goalkeeper hierarchy

`titolari.csv` carries `gerarchia_portiere` for active goalkeepers. Canonical
values are `PRIMO`, `SECONDO`, `TERZO`, and contiguous slash-separated contests
such as `PRIMO/SECONDO` or `SECONDO/TERZO`. Generation rejects unknown,
non-contiguous, unresolved, duplicate, or outfield assignments and preserves
the hierarchy in `auction_data.json`.

The source note remains the evidence and observation context. Missing ranks are
left unknown rather than inferred from FVM, names, or absence from an article.

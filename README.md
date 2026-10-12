# U.S. Funding Stress Dashboard

Cloudflare Worker + D1 dashboard for funding rates, Treasury settlements and coupon-adjusted liquidity risk.

The entire SOX correlation section was removed at the user's request on 2026-10-13, including its daily, original and overfit tabs. The dashboard loads only the funding panels. The existing data collector, research history, Worker hourly cron, D1 binding and ingest secret remain in place.

## Retained SOX data and research

- Original sample: six U1/drawdown points and eight U4/rebound points, with separate descriptive regressions.
- Daily sample: every eligible session under `tga-public-face-residual-v1`, with no outcome-based event selection.
- The SOX API and collection retain the original and daily samples separately; the dashboard does not request or render them.
- The model measures absolute mismatch magnitude, not a signed net cash drain: `U1 = |ΔTGA − N|`; `ΔTGA` is a balance change. Reversing the subtraction gives the same magnitude. U4 sums the signed residuals first.
- Missing inputs and prices remain unavailable. A verified no-principal day may be a genuine zero; an unverified maturity cannot.
- The model uses face-value issuance less SOMA and verified public maturity estimates. It is separate from the existing coupon-adjusted cash-flow heatmap.
- Input revisions retain hashes and the first complete observation time. Outcomes require the following four/five market sessions.

`config/sox-model.json` retains `sox.public_use_confirmed=false` and records the user's confirmed personal, noncommercial research use as `sox.personal_use_confirmed=true`. This mode downloads SOX closes and retains derived results with Nasdaq/FRED source metadata. It does not claim a general service or redistribution license. [FRED's terms](https://fred.stlouisfed.org/legal/) allow personal or noncommercial educational use of pre-approval series; other uses require the data owner's permission. [NASDAQSOX](https://fred.stlouisfed.org/series/NASDAQSOX) is owned by Nasdaq.

The two confirmations are separate. Turning both off stops SOX collection and hides derived outcomes (`LICENSE_REQUIRED`), while retaining private revision history and continuing Treasury updates. Raw SOX closes remain restricted to the authenticated inputs endpoint in either mode. The daily FRED CSV download uses curl, which is available on the existing GitHub Ubuntu runner, with request/time/size limits. It does not scrape the HTML graph page.

The original CSV estimates and selected events are preserved. The original selection algorithm is not reconstructed, and these regressions are descriptive associations.

The overfit chart renderer and its request-time calculation were removed. `/api/sox` no longer returns an `overfit` field or queries private daily inputs to build that chart. Past exploration reports remain under `research/`.

The separate daily sample now backfills the available source window with historical monthly MSPD inventories. Each day uses the latest inventory strictly before that date. Auctions include issues after the oldest inventory, so bills issued before the chart's start cannot disappear from its maturity schedule. Weekend/holiday maturity announcements use the actual Fed business payment day. Dates with missing inputs remain pending.

```text
d_t = ΔTGA_t − N_t
U1_t = |d_t|
U4_t = |d_t + d_(t−1) + d_(t−2) + d_(t−3)|
4-session drawdown = 100 × [1 − min(P_t,…,P_(t+4)) / P_t]
session-5 rebound = 100 × [P_(t+5) / min(P_t,…,P_(t+4)) − 1]
```

The existing `Treasury collector` workflow has one additional SOX step. Both original schedules stay unchanged. After the initial/configuration-change run, the daily gate opens at 18:00 America/New_York. Failed attempts remain eligible for retry; successful snapshots are not repeated that day.

If FRED has not yet published the latest closed market session, the collector keeps prior points and reports `WAITING_SOURCE_UPDATE`. That attempt does not complete the daily gate: the next existing workflow run retries, including across midnight when publication arrives late. Weekends and market holidays use the latest actual trading session rather than wait for a nonexistent close.

## Local checks

From the repository root:

```bash
npm ci
python3 scripts/sox_selftest.py
python3 -m unittest discover -s tests -p test_sox_collector.py -v
node tests/test_sox_worker.mjs
npx wrangler deploy --dry-run
```

The runtime test writes fixtures to a local Worker only. Initialize its local D1, then run the Worker with a local test token:

```bash
npx wrangler d1 execute funding-dashboard-db --local --file=./schema.sql
npx wrangler d1 execute funding-dashboard-db --local --file=./migrations/0033_sox.sql
npx wrangler dev --local --ip 127.0.0.1 --port 8787 --var TREASURY_INGEST_TOKEN:local-sox-test-only
```

In another terminal:

```bash
node tests/test_sox_runtime.mjs
node tests/test_dashboard_browser.mjs
```

The dashboard browser test is read-only. It verifies the remaining funding panels, loaded values, settlement rows, liquidity rows, mobile width and absence of SOX requests, including an old `#sox-research` bookmark. It also accepts the production dashboard URL as an explicit argument. The test requires Playwright and its Chromium headless shell; `PLAYWRIGHT_MODULE`, `PLAYWRIGHT_BROWSERS_PATH` and optional `DASHBOARD_SCREENSHOT_DIR` can point to an existing test environment.

Runtime fixtures should use a separate disposable local D1 rather than overwrite real inputs. They contain synthetic prices, and the runtime test rejects production URLs.

## Exploratory factor study

The user-requested overfit experiment is isolated in [research/sox-factor-search/report.html](research/sox-factor-search/report.html). It compares predefined Treasury/market/calendar factor recipes, post-hoc X-only subgroup searches, explicitly outcome-selected eight-point demonstrations, and a flexible nonlinear training fit. It also reports chronological diagnostics with overlapping outcome windows removed at the split boundary. It does not change the collector, chart model, original sample, schedules or production data.

`research/sox-factor-search/analyze.py` reads a real local SOX SQLite snapshot in read-only mode and checks its baseline values against a saved public production API response. It requires numpy/pandas, with matplotlib for `--plots`. Pass `--database <local-sqlite-path>` and `--production-snapshot <matched-api-json-path>`. Results include derived factor/outcome CSVs, factor definitions, selection dates, JSON metrics and PNG/SVG figures; raw SOX closes are excluded. Training-only selection, future-input perturbation and split-boundary checks run before writing results.

High correlations selected after searching the complete sample are exploratory, not validation scores. The later-period split is diagnostic on an already inspected short dataset, not a pristine prospective trading backtest. Treasury inputs are retrospective; the study does not reconstruct historical coupon-adjusted net cash.

After the user clarified that the intended scope was every valid date since 2026-07-20 and single factors only, [the corrected full-period study](research/sox-full-period/report.html) independently fetched July 20–22 and prior context from official sources. Every candidate uses the same 55 drawdown or 54 rebound dates through the available 2026-10-09 closes; no date gates or fitted factor combinations are used. The 4,635 recipes include up to 60-session windows and 40-session lags. Maximum absolute full-sample correlations are about 0.66. This archived research does not write production inputs and remains available after removal of the dashboard's SOX section.

## Production rollout

Production migration, deployment and main changes require the user's approval. For the initial installation, apply `migrations/0033_sox.sql` to the existing D1 before deploying; the migration creates three SOX tables and does not replace the existing schema or database. Chart removal requires no migration or data deletion. Deploy the verified Worker, check the live results, then publish the repository changes to main.

Keep the current `wrangler.jsonc`, D1 ID, Worker URL, cron schedules and `TREASURY_INGEST_TOKEN`. Keep the general SOX public-use flag disabled until permission for that scope is confirmed. The user's personal research confirmation enables the existing workflow to collect Treasury and SOX inputs and retain the daily results without adding a workflow or schedule.

Retained data endpoints: `/api/sox` and `/api/sox/status`. The former serves the derived results for the confirmed analysis scope. `/api/sox/state` and `/api/sox/ingest` require the existing ingest token. Responses exclude raw SOX closes at both top level and inside nested report structures, and hide outcomes when neither scope is confirmed. The funding dashboard does not call these endpoints.

See [the implementation and validation record](SOX_V33_VALIDATION.md) for the fixes, executed checks and remaining production verification.

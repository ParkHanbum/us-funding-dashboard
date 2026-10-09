# U.S. Funding Stress Dashboard

Cloudflare Worker + D1 dashboard for funding rates, Treasury settlements, coupon-adjusted liquidity risk and SOX liquidity research.

The v3.3 SOX module adds a fixed original research scatterplot and a separate daily model. The existing dashboard, Treasury collector, Worker hourly cron, D1 binding and ingest secret remain in place.

## SOX research

- Original sample: six U1/drawdown points and eight U4/rebound points, with separate descriptive regressions.
- Daily sample: every eligible session under `tga-public-face-residual-v1`, with no outcome-based event selection.
- The daily chart is the default view. The fixed original sample stays in a separate tab; fresh results update the displayed points and regressions on the existing five-minute refresh.
- Missing inputs and prices remain unavailable. A verified no-principal day may be a genuine zero; an unverified maturity cannot.
- The model uses face-value issuance less SOMA and verified public maturity estimates. It is separate from the existing coupon-adjusted cash-flow heatmap.
- Input revisions retain hashes and the first complete observation time. Outcomes require the following four/five market sessions.

`config/sox-model.json` retains `sox.public_use_confirmed=false`. Treasury inputs update, but the collector does not download new SOX prices or publish new SOX outcomes. `LICENSE_REQUIRED` is the expected state until public data use is confirmed. [FRED's NASDAQSOX page](https://fred.stlouisfed.org/series/NASDAQSOX) identifies the data as copyrighted and requiring pre-approval.

The original CSV estimates and selected events are preserved. The original selection algorithm is not reconstructed, and these regressions are descriptive associations.

The separate daily sample now backfills the available source window with historical monthly MSPD inventories. Each day uses the latest inventory strictly before that date. Auctions include issues after the oldest inventory, so bills issued before the chart's start cannot disappear from its maturity schedule. Weekend/holiday maturity announcements use the actual Fed business payment day. Dates with missing inputs remain pending.

```text
d_t = ΔTGA_t − N_t
U1_t = |d_t|
U4_t = |d_t + d_(t−1) + d_(t−2) + d_(t−3)|
4-session drawdown = 100 × [1 − min(P_t,…,P_(t+4)) / P_t]
session-5 rebound = 100 × [P_(t+5) / min(P_t,…,P_(t+4)) − 1]
```

The existing `Treasury collector` workflow has one additional SOX step. Both original schedules stay unchanged. After the initial/configuration-change run, the daily gate opens at 18:00 America/New_York. Failed attempts remain eligible for retry; successful snapshots are not repeated that day.

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
node tests/test_sox_browser.mjs
```

The browser test requires Playwright and its Chromium headless shell. `PLAYWRIGHT_MODULE`, `PLAYWRIGHT_BROWSERS_PATH` and `SOX_SCREENSHOT_DIR` can point to an existing test environment. Runtime fixtures should use a disposable local D1; they contain synthetic prices, and the tests reject production URLs.

## Production rollout

Production migration, deployment and main changes require the user's approval. After approval, apply `migrations/0033_sox.sql` to the existing D1, deploy this Worker, then publish the verified repository changes to main. The migration creates three SOX tables and does not replace the existing schema or database.

Keep the current `wrangler.jsonc`, D1 ID, Worker URL, cron schedules and `TREASURY_INGEST_TOKEN`. Keep SOX public use disabled until the relevant permission is confirmed. The next existing workflow run can then collect Treasury inputs and report its SOX state.

Public endpoints: `/api/sox` and `/api/sox/status`. `/api/sox/state` and `/api/sox/ingest` require the existing ingest token. Public responses exclude raw SOX closes at both top level and inside nested report structures.

See [the implementation and validation record](SOX_V33_VALIDATION.md) for the fixes, executed checks and remaining production verification.

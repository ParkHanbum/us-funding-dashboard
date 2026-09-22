# U.S. Funding Stress Dashboard v2.4

## What changed

The live screenshot confirmed:

- Daily TGA browser-direct FiscalData fetch works.
- TreasuryDirect browser fetch fails, and the page falls back to an empty Worker cache.

v2.4 therefore uses the same working U.S. Treasury FiscalData origin for auctions:

- Actual / historical auction rows:
  `/v1/accounting/od/auctions_query`
- Upcoming auctions:
  `/v1/accounting/od/upcoming_auctions`

The dashboard reads those APIs directly in the visitor browser and groups securities by `issue_date`, which is the cash settlement date.

## Deploy

Copy your existing D1 `database_id` into `wrangler.jsonc`.

```bash
npm install
npm run db:init:remote
npm run deploy
```

No secret change is required.

Then hard-refresh the dashboard:

```text
https://us-funding-dashboard.nb2sy.workers.dev/
```

Expected result:
- TGA still shows Treasury direct daily data.
- Upcoming Treasury settlements should show grouped dates and ACTUAL / TENTATIVE rows.
- `/api/settlements` may remain empty because Treasury settlement data are now intentionally fetched in the browser.

## Next version

After this list renders, v3 will add:
- publicly held maturity / redemption
- Bill discount cash proceeds
- coupon reopening settlement cash
- TIPS settlement adjustment
- true net cash drain / addition
- 5-business-day liquidity risk heatmap

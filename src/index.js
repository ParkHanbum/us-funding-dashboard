
const NYFED_RATES = "https://markets.newyorkfed.org/api/rates/all/latest.json";
const NYFED_RRP = "https://markets.newyorkfed.org/api/rp/reverserepo/all/results/last/10.json";
const NYFED_SRF = "https://markets.newyorkfed.org/api/rp/repo/all/results/last/20.json";
const TREASURY_AUCTIONED =
  "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&day=7";
const TREASURY_ANNOUNCED =
  "https://www.treasurydirect.gov/TA_WS/securities/announced?format=json&pagesize=100";
const FED_TGA_PREVIEW =
  "https://www.federalreserve.gov/datadownload/Preview.aspx?pi=5&preview=H41%2FH41%2FRESPPLLDT_N.WW&rel=H41";
const FED_RESERVES_PREVIEW =
  "https://www.federalreserve.gov/datadownload/Preview.aspx?pi=5&preview=H41%2FH41%2FRESH4R_N.WW&rel=H41";
const FED_IORB_TABLE =
  "https://www.federalreserve.gov/datadownload/DownloadTable.aspx?filetype=csv&label=include&layout=seriescolumn&rel=PRATES&series=c27939ee810cb2e929a920a6bd77d9f6&type=package";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/health")
      return json({ ok: true, now: new Date().toISOString() });

    if (url.pathname === "/api/summary")
      return json(await buildSummary(env.DB));

    if (url.pathname === "/api/settlements")
      return json(await buildSettlementCalendar(env.DB, 12));

    if (url.pathname === "/api/liquidity")
      return json(await buildLiquidityCalendar(env.DB, 5));

    if (url.pathname === "/api/debug/auctions") {
      const r = await env.DB.prepare(`
        SELECT cusip,security_type,security_term,auction_date,issue_date,maturity_date,
               offering_amt,total_accepted,status,fetched_at
        FROM auctions ORDER BY fetched_at DESC, auction_date DESC LIMIT 20
      `).all();
      return json({ count: r.results?.length || 0, rows: r.results || [] });
    }

    if (url.pathname === "/api/debug/runs") {
      const r = await env.DB.prepare(`
        SELECT id,started_at,finished_at,ok,message
        FROM collection_runs ORDER BY id DESC LIMIT 10
      `).all();
      return json({ rows: r.results || [] });
    }

    if (url.pathname === "/api/history") {
      const metric = url.searchParams.get("metric") || "SOFR";
      const limit = Math.min(Number(url.searchParams.get("limit") || 60), 500);
      const result = await env.DB.prepare(
        `SELECT metric, observed_at, value, unit
         FROM metrics WHERE metric=?
         ORDER BY observed_at DESC LIMIT ?`
      ).bind(metric, limit).all();
      return json({ metric, data: result.results.reverse() });
    }


    if (url.pathname === "/api/treasury-ingest" && request.method === "POST") {
      const auth = request.headers.get("authorization");
      if (!env.TREASURY_INGEST_TOKEN ||
          auth !== `Bearer ${env.TREASURY_INGEST_TOKEN}`) {
        return json({ error: "unauthorized" }, 401);
      }

      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: "invalid_json" }, 400);
      }

      if (!Array.isArray(body?.auctions)) {
        return json({ error: "auctions_array_required" }, 400);
      }

      const sql = `
        INSERT INTO auctions(
          cusip,security_type,security_term,auction_date,issue_date,maturity_date,
          offering_amt,total_accepted,soma_accepted,price_per_100,status,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(cusip,auction_date) DO UPDATE SET
          security_type=excluded.security_type,
          security_term=excluded.security_term,
          issue_date=excluded.issue_date,
          maturity_date=excluded.maturity_date,
          offering_amt=COALESCE(excluded.offering_amt,auctions.offering_amt),
          total_accepted=COALESCE(excluded.total_accepted,auctions.total_accepted),
          soma_accepted=COALESCE(excluded.soma_accepted,auctions.soma_accepted),
          price_per_100=COALESCE(excluded.price_per_100,auctions.price_per_100),
          status=CASE WHEN excluded.status='actual' THEN 'actual' ELSE auctions.status END,
          fetched_at=datetime('now'),
          raw_json=excluded.raw_json
      `;

      const statements = [];
      let skipped = 0;

      for (const row of body.auctions) {
        if (!row?.cusip || !row?.auction_date) {
          skipped++;
          continue;
        }

        // Treasury collector sends currency in raw USD.
        // D1's historical dashboard convention is USD billions.
        statements.push(
          env.DB.prepare(sql).bind(
            row.cusip,
            row.security_type ?? null,
            row.security_term ?? null,
            row.auction_date,
            row.issue_date ?? null,
            row.maturity_date ?? null,
            usdToBn(row.offering_amt),
            usdToBn(row.total_accepted),
            usdToBn(row.soma_accepted),
            finiteOrNull(row.price_per_100),
            row.status === "actual" ? "actual" : "tentative",
            JSON.stringify(row)
          )
        );
      }

      if (statements.length)
        await env.DB.batch(statements);

      return json({
        ok: true,
        accepted: statements.length,
        skipped,
        source: body.source || "github-actions",
        batch: body.batch ?? null,
        collected_at: body.collected_at || null
      });
    }

    if (url.pathname === "/api/refresh" && request.method === "POST") {
      const auth = request.headers.get("authorization");
      if (!env.REFRESH_TOKEN || auth !== `Bearer ${env.REFRESH_TOKEN}`)
        return json({ error: "unauthorized" }, 401);
      return json(await collectAll(env));
    }

    return env.ASSETS.fetch(request);
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(collectAll(env));
  }
};

async function collectAll(env) {
  const started = new Date().toISOString();
  const run = await env.DB.prepare(
    "INSERT INTO collection_runs(started_at, ok) VALUES (?,0)"
  ).bind(started).run();

  const tasks = [
    collectNyFedRates,
    collectRrp,
    collectSrf,
    collectFedOfficial
  ];

  let ok = true;
  const messages = [];
  for (const task of tasks) {
    try { messages.push(await task(env.DB)); }
    catch (e) {
      ok = false;
      messages.push(`${task.name}: ${e?.message || e}`);
    }
  }

  await env.DB.prepare(
    "UPDATE collection_runs SET finished_at=?,ok=?,message=? WHERE id=?"
  ).bind(new Date().toISOString(), ok ? 1 : 0, messages.join(" | "), run.meta.last_row_id).run();

  return { ok, messages };
}

async function collectNyFedRates(db) {
  const body = await fetchJson(NYFED_RATES);
  const wanted = new Set(["SOFR","EFFR","TGCR","BGCR"]);
  let count = 0;

  for (const r of body.refRates || []) {
    if (!wanted.has(r.type) || typeof r.percentRate !== "number") continue;
    await upsertMetric(db, "NYFED", r.type, r.effectiveDate, r.percentRate, "percent", r);

    if (r.type === "SOFR") {
      const extras = [
        ["percentPercentile25","SOFR_P25","percent"],
        ["percentPercentile75","SOFR_P75","percent"],
        ["percentPercentile99","SOFR_P99","percent"],
        ["volumeInBillions","SOFR_VOLUME","USD bn"]
      ];
      for (const [key, metric, unit] of extras)
        if (typeof r[key] === "number")
          await upsertMetric(db, "NYFED", metric, r.effectiveDate, r[key], unit, r);
    }
    count++;
  }
  return `NYFED_RATES:${count}`;
}

async function collectRrp(db) {
  const body = await fetchJson(NYFED_RRP);
  const ops = body?.repo?.operations || [];
  let count = 0;
  for (const op of ops) {
    if (op.auctionStatus !== "Results") continue;
    const valueBn = Number(op.totalAmtAccepted) / 1e9;
    if (!Number.isFinite(valueBn)) continue;
    await upsertMetric(db, "NYFED", "ON_RRP", op.operationDate, valueBn, "USD bn", op);
    if (Number.isFinite(Number(op.participatingCpty)))
      await upsertMetric(db, "NYFED", "ON_RRP_CPTY", op.operationDate, Number(op.participatingCpty), "count", op);
    count++;
  }
  return `ON_RRP:${count}`;
}

async function collectSrf(db) {
  const body = await fetchJson(NYFED_SRF);
  const ops = body?.repo?.operations || [];
  const byDate = new Map();

  for (const op of ops) {
    if (op.auctionStatus !== "Results") continue;
    const date = op.operationDate;
    const accepted = Number(op.totalAmtAccepted);
    if (!date || !Number.isFinite(accepted)) continue;
    byDate.set(date, (byDate.get(date) || 0) + accepted);
  }

  for (const [date, accepted] of byDate)
    await upsertMetric(db, "NYFED", "SRF_USAGE", date, accepted / 1e9, "USD bn", { summedFromOperations: true });

  return `SRF_DAYS:${byDate.size}`;
}


async function collectFedOfficial(db) {
  let count = 0;

  // IORB: Federal Reserve official policy-rates table.
  const iorbHtml = await fetchText(FED_IORB_TABLE);
  const iorbText = htmlToText(iorbHtml);
  const rowStart = iorbText.indexOf("RESBM_N.D");
  if (rowStart >= 0) {
    const row = iorbText.slice(rowStart, rowStart + 1400);
    const nums = [...row.matchAll(/\b(\d+\.\d+)\b/g)].map(m => Number(m[1]));
    const latest = nums.length ? nums[nums.length - 1] : null;
    if (Number.isFinite(latest)) {
      await upsertMetric(db, "FED", "IORB", isoDate(new Date()), latest, "percent", { source: FED_IORB_TABLE });
      count++;
    }
  }

  // Exact H.4.1 series, avoiding neighboring-row HTML parsing.
  const tga = await fetchFedPreviewObservation(FED_TGA_PREVIEW);
  if (tga) {
    await upsertMetric(db, "FED_H41", "TGA_H41", tga.date, tga.value / 1000, "USD bn", {
      series: "RESPPLLDT_N.WW",
      source: FED_TGA_PREVIEW
    });
    count++;
  }

  const reserves = await fetchFedPreviewObservation(FED_RESERVES_PREVIEW);
  if (reserves) {
    await upsertMetric(db, "FED_H41", "RESERVES", reserves.date, reserves.value / 1000, "USD bn", {
      series: "RESH4R_N.WW",
      source: FED_RESERVES_PREVIEW
    });
    count++;
  }

  return `FED_OFFICIAL:${count}`;
}

async function fetchFedPreviewObservation(url) {
  const html = await fetchText(url);
  const text = htmlToText(html);
  // Preview output contains: Unique ID | Time Period | Value, newest observation first.
  const m = text.match(/\b(20\d{2}-\d{2}-\d{2})\b\s+([0-9][0-9,]*)/);
  if (!m) throw new Error(`Unable to parse Fed preview ${url}`);
  return {
    date: m[1],
    value: Number(m[2].replaceAll(",", ""))
  };
}


async function collectTreasuryAuctions(db) {
  const recent = await fetchJson(TREASURY_AUCTIONED);
  const announced = await fetchJson(TREASURY_ANNOUNCED);
  let count = 0;

  for (const r of Array.isArray(recent) ? recent : []) {
    await upsertAuction(db, normalizeTreasuryDirect(r), "actual");
    count++;
  }
  for (const r of Array.isArray(announced) ? announced : []) {
    await upsertAuction(db, normalizeTreasuryDirect(r), "tentative");
    count++;
  }

  const sample = await db.prepare(`
    SELECT issue_date FROM auctions
    WHERE issue_date IS NOT NULL
    ORDER BY issue_date DESC LIMIT 1
  `).first();
  return `TREASURYDIRECT:${count}${sample?.issue_date ? ` latest_issue=${sample.issue_date}` : ""}`;
}

function normalizeTreasuryDirect(r) {
  return {
    cusip: r.cusip,
    security_type: r.securityType,
    security_term: r.securityTerm,
    auction_date: dateOnly(r.auctionDate),
    issue_date: dateOnly(r.issueDate),
    maturity_date: dateOnly(r.maturityDate),
    offering_amt: r.offeringAmount,
    total_accepted: r.totalAccepted,
    soma_accepted: r.somaAccepted,
    unadj_price: r.unadjustedPrice || r.pricePer100 || r.highPrice,
    adj_price: r.adjustedPrice,
    raw: r
  };
}

async function upsertAuction(db, r, status) {
  const cusip = clean(r.cusip || r.announced_cusip || r.announced_cusip_number);
  const auctionDate = clean(r.auction_date);
  if (!cusip || !auctionDate) return;

  const offering = moneyToBn(r.offering_amt);
  const accepted = moneyToBn(r.total_accepted);
  const soma = moneyToBn(r.soma_accepted);
  const px = firstNumber(r.unadj_price, r.adj_price, r.high_price, r.price_per100);

  // completed auction rows override tentative rows for the same CUSIP/date
  await db.prepare(`
    INSERT INTO auctions(
      cusip,security_type,security_term,auction_date,issue_date,maturity_date,
      offering_amt,total_accepted,soma_accepted,price_per_100,status,raw_json
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(cusip,auction_date) DO UPDATE SET
      security_type=excluded.security_type,
      security_term=excluded.security_term,
      issue_date=excluded.issue_date,
      maturity_date=excluded.maturity_date,
      offering_amt=COALESCE(excluded.offering_amt,auctions.offering_amt),
      total_accepted=COALESCE(excluded.total_accepted,auctions.total_accepted),
      soma_accepted=COALESCE(excluded.soma_accepted,auctions.soma_accepted),
      price_per_100=COALESCE(excluded.price_per_100,auctions.price_per_100),
      status=CASE WHEN excluded.status='actual' THEN 'actual' ELSE auctions.status END,
      fetched_at=datetime('now'),
      raw_json=excluded.raw_json
  `).bind(
    cusip, clean(r.security_type), clean(r.security_term), auctionDate,
    clean(r.issue_date), clean(r.maturity_date),
    offering, accepted, soma, px, status, JSON.stringify(r.raw ?? r)
  ).run();
}

async function buildSummary(db) {
  const names = [
    "SOFR","EFFR","TGCR","BGCR","SOFR_P99","SOFR_VOLUME",
    "IORB","TGA_H41","RESERVES","ON_RRP","ON_RRP_CPTY","SRF_USAGE"
  ];
  const pairs = await Promise.all(names.map(async n => [n, await latest(db, n)]));
  const m = Object.fromEntries(pairs);
  if (!m.TGA && m.TGA_H41) m.TGA = m.TGA_H41;

  const tgaRows = await recent(db, "TGA_H41", 12);
  const stale = [];
  if (m.SOFR && m.EFFR && m.SOFR.observed_at !== m.EFFR.observed_at)
    stale.push("SOFR/EFFR date mismatch");

  const sofrIorb = spreadBp(m.SOFR, m.IORB);
  const srfBn = m.SRF_USAGE?.value ?? 0;

  let classification = "NEUTRAL";
  if (sofrIorb !== null && sofrIorb <= -2 && srfBn < 1) classification = "EASING";
  if (sofrIorb !== null && sofrIorb >= 3) classification = "WORSENING";
  if (srfBn >= 1) classification = "WORSENING";

  return {
    generatedAt: new Date().toISOString(),
    classification,
    stale,
    metrics: m,
    spreadsBp: {
      sofrEffr: spreadBp(m.SOFR, m.EFFR),
      sofrIorb: spreadBp(m.SOFR, m.IORB),
      effrIorb: spreadBp(m.EFFR, m.IORB)
    },
    tgaChangesBn: {
      oneDay: diff(tgaRows,0,1),
      fiveBusinessDays: diff(tgaRows,0,5),
      weekOverWeek: diff(tgaRows,0,5)
    }
  };
}

async function buildSettlementCalendar(db, daysAhead=12) {
  const today = new Date();
  const from = isoDate(today);
  const to = isoDate(new Date(today.getTime() + daysAhead*86400000));

  const r = await db.prepare(`
    SELECT cusip,security_type,security_term,auction_date,issue_date,maturity_date,
           offering_amt,total_accepted,soma_accepted,price_per_100,status,raw_json
    FROM auctions
    WHERE issue_date >= ? AND issue_date <= ?
    ORDER BY issue_date, security_type, security_term
  `).bind(from,to).all();

  const groups = {};
  for (const row of r.results || []) {
    const d = row.issue_date;
    if (!d) continue;

    if (!groups[d]) groups[d] = {
      settlementDate: d,
      grossFaceBn: 0,
      actualFaceBn: 0,
      tentativeFaceBn: 0,
      rows: []
    };

    const faceBn = publicIssuanceBn(row);
    groups[d].grossFaceBn += faceBn ?? 0;

    if (row.status === "actual")
      groups[d].actualFaceBn += faceBn ?? 0;
    else
      groups[d].tentativeFaceBn += faceBn ?? 0;

    groups[d].rows.push({
      ...row,
      public_face_bn: faceBn,
      offering_amt: storedMoneyBn(row.offering_amt),
      total_accepted: storedMoneyBn(row.total_accepted),
      soma_accepted: storedMoneyBn(row.soma_accepted)
    });
  }

  return {
    from,
    to,
    note: "Treasury auction data is collected by GitHub Actions and stored in D1. Amounts returned by this endpoint are USD billions.",
    days: Object.values(groups)
  };
}

async function buildLiquidityCalendar(db, businessDays=5) {
  const dates = nextBusinessDates(new Date(), businessDays);
  if (!dates.length)
    return { generatedAt: new Date().toISOString(), days: [] };

  const from = dates[0];
  const to = dates[dates.length - 1];

  const r = await db.prepare(`
    SELECT cusip,security_type,security_term,auction_date,issue_date,maturity_date,
           offering_amt,total_accepted,soma_accepted,price_per_100,status,raw_json
    FROM auctions
    WHERE issue_date >= ? AND issue_date <= ?
    ORDER BY issue_date, auction_date, security_type, security_term
  `).bind(from,to).all();

  const rowsByDate = new Map(dates.map(d => [d, []]));
  for (const row of r.results || []) {
    if (rowsByDate.has(row.issue_date))
      rowsByDate.get(row.issue_date).push(row);
  }

  const days = dates.map(date => {
    const rows = rowsByDate.get(date) || [];

    let issuanceFaceBn = 0;
    let actualIssuanceBn = 0;
    let tentativeIssuanceBn = 0;
    let cashProceedsBn = 0;
    let cashKnown = rows.length > 0;

    // Treasury announcements often repeat the same "publicly held
    // maturities by type" figure across several auctions settling that day.
    // Deduplicate by settlement date + maturing date + broad security type.
    const maturityMap = new Map();

    for (const row of rows) {
      const face = publicIssuanceBn(row) ?? 0;
      issuanceFaceBn += face;
      if (row.status === "actual") actualIssuanceBn += face;
      else tentativeIssuanceBn += face;

      const raw = parseRaw(row.raw_json);
      const px = firstFinite(
        raw.adjusted_price,
        raw.price_per_100,
        row.price_per_100
      );
      const accrued = firstFinite(
        raw.adjusted_accrued_interest_per_100,
        raw.accrued_interest_per_100,
        raw.unadjusted_accrued_interest_per_100
      );

      if (row.status === "actual" && px != null) {
        // Principal purchase cash. For coupon securities add auction accrued
        // interest when the source exposes it.
        const settlementPer100 = px + (accrued ?? 0);
        cashProceedsBn += face * settlementPer100 / 100;
      } else {
        cashKnown = false;
      }

      const matBn = rawMoneyBn(raw.est_pub_held_mat_by_type_amt);
      const matDate = raw.maturing_date || raw.mat_date || row.issue_date;
      if (matBn != null && matDate) {
        const bucket = maturityBucket(row.security_type);
        const key = `${date}|${matDate}|${bucket}`;
        const old = maturityMap.get(key);
        // Duplicate rows normally carry the same aggregate. max() is safer
        // than summing repeated announcement metadata.
        if (old == null || matBn > old)
          maturityMap.set(key, matBn);
      }
    }

    const publicMaturityBn = [...maturityMap.values()]
      .reduce((a,b) => a+b, 0);

    const hasMaturityEstimate = maturityMap.size > 0;
    const netPrincipalDrainBn = hasMaturityEstimate
      ? issuanceFaceBn - publicMaturityBn
      : null;

    const netCashEstimateBn = hasMaturityEstimate && cashKnown
      ? cashProceedsBn - publicMaturityBn
      : null;

    return {
      date,
      issuanceFaceBn: round3(issuanceFaceBn),
      actualIssuanceBn: round3(actualIssuanceBn),
      tentativeIssuanceBn: round3(tentativeIssuanceBn),
      publicMaturityBn: hasMaturityEstimate ? round3(publicMaturityBn) : null,
      netPrincipalDrainBn: netPrincipalDrainBn == null ? null : round3(netPrincipalDrainBn),
      cashProceedsBn: cashKnown ? round3(cashProceedsBn) : null,
      netCashEstimateBn: netCashEstimateBn == null ? null : round3(netCashEstimateBn),
      status: rows.some(r => r.status !== "actual") ? "tentative" : (rows.length ? "actual" : "none"),
      risk: settlementRisk(netPrincipalDrainBn),
      confidence: hasMaturityEstimate
        ? (cashKnown ? "cash-estimate" : "principal-only")
        : "maturity-pending",
      rows: rows.map(row => ({
        cusip: row.cusip,
        security_type: row.security_type,
        security_term: row.security_term,
        auction_date: row.auction_date,
        issue_date: row.issue_date,
        status: row.status,
        public_face_bn: publicIssuanceBn(row)
      }))
    };
  });

  return {
    generatedAt: new Date().toISOString(),
    from,
    to,
    methodology: {
      issuance: "Public issuance = total accepted minus SOMA when auction results are available; otherwise announced offering amount.",
      maturity: "Treasury announcement field: estimated publicly held maturing securities by type, deduplicated within each settlement date.",
      netPrincipalDrain: "Positive = Treasury raises more principal than is redeemed (liquidity drain); negative = principal liquidity addition.",
      netCashEstimate: "Price-adjusted auction proceeds minus public principal maturities. Coupon payments and some TIPS/indexation effects are not yet included.",
      risk: "Dashboard heuristic: HIGH >= $75bn drain, MODERATE >= $25bn, LOW otherwise; PENDING when maturity estimate is unavailable."
    },
    days
  };
}

function publicIssuanceBn(row) {
  const offering = storedMoneyBn(row.offering_amt);
  const accepted = storedMoneyBn(row.total_accepted);
  const soma = storedMoneyBn(row.soma_accepted);

  if (row.status === "actual" && accepted != null) {
    const publicAccepted = accepted - (soma ?? 0);
    if (publicAccepted > 0)
      return publicAccepted;
  }
  return offering ?? accepted ?? 0;
}

function storedMoneyBn(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;

  // Seamless migration: the first GitHub ingest revision accidentally
  // stored raw dollars. New ingests store billions.
  return Math.abs(n) > 1_000_000 ? n / 1e9 : n;
}

function rawMoneyBn(value) {
  const n = Number(String(value ?? "").replaceAll(",", ""));
  if (!Number.isFinite(n)) return null;
  return Math.abs(n) > 1_000_000 ? n / 1e9 : n;
}

function usdToBn(value) {
  const n = Number(String(value ?? "").replaceAll(",", ""));
  return Number.isFinite(n) ? n / 1e9 : null;
}

function finiteOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function firstFinite(...values) {
  for (const value of values) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function parseRaw(raw) {
  if (!raw) return {};
  try { return typeof raw === "string" ? JSON.parse(raw) : raw; }
  catch { return {}; }
}

function maturityBucket(securityType) {
  const s = String(securityType || "").toLowerCase();
  if (s.includes("bill")) return "bill";
  if (s.includes("tips")) return "tips";
  if (s.includes("frn") || s.includes("floating")) return "frn";
  if (s.includes("bond")) return "bond";
  if (s.includes("note")) return "note";
  return s || "unknown";
}

function nextBusinessDates(start, count) {
  const out = [];
  const d = new Date(Date.UTC(
    start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()
  ));
  while (out.length < count) {
    const dow = d.getUTCDay();
    if (dow !== 0 && dow !== 6)
      out.push(isoDate(d));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

function settlementRisk(netDrainBn) {
  if (netDrainBn == null) return "PENDING";
  if (netDrainBn >= 75) return "HIGH";
  if (netDrainBn >= 25) return "MODERATE";
  return "LOW";
}

function round3(v) {
  return Number(Number(v).toFixed(3));
}


async function fetchText(url) {
  const r = await fetch(url, { headers: { "user-agent": "us-funding-dashboard/0.2.1" } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return await r.text();
}

function htmlToText(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;|&#160;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function chunkAfter(text, needle, n=250) {
  const i = text.indexOf(needle);
  return i >= 0 ? text.slice(i, i+n) : "";
}

function extractCommaNumbers(text) {
  return [...text.matchAll(/(?<![\d.])([+-]?\s*\d{1,3}(?:,\d{3})+)(?![\d.])/g)]
    .map(m => Number(m[1].replace(/\s/g, "").replaceAll(",", "")))
    .filter(Number.isFinite);
}

function parseH41Wednesday(text) {
  const m = text.match(/Wednesday\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})/);
  if (!m) return null;
  const months = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
  return isoDate(new Date(Date.UTC(Number(m[3]), months[m[1]], Number(m[2]))));
}

function dateOnly(v) {
  if (!v) return null;
  const s = String(v).trim();

  // TreasuryDirect usually returns YYYY-MM-DDT00:00:00.
  let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;

  // Be defensive if an endpoint switches to MM/DD/YYYY.
  m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (m)
    return `${m[3]}-${String(m[1]).padStart(2,"0")}-${String(m[2]).padStart(2,"0")}`;

  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0,10);
}

async function fetchJson(url) {
  const r = await fetch(url, { headers: { "user-agent": "us-funding-dashboard/0.2" } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return await r.json();
}

async function upsertMetric(db, source, metric, observedAt, value, unit, raw) {
  await db.prepare(`
    INSERT INTO metrics(source,metric,observed_at,value,unit,raw_json)
    VALUES(?,?,?,?,?,?)
    ON CONFLICT(metric,observed_at) DO UPDATE SET
      value=excluded.value,unit=excluded.unit,fetched_at=datetime('now'),
      raw_json=excluded.raw_json
  `).bind(source,metric,observedAt,value,unit,JSON.stringify(raw)).run();
}

async function latest(db, metric) {
  return await db.prepare(`
    SELECT metric,observed_at,value,unit,fetched_at
    FROM metrics WHERE metric=? ORDER BY observed_at DESC LIMIT 1
  `).bind(metric).first();
}

async function recent(db, metric, limit=12) {
  const r = await db.prepare(`
    SELECT observed_at,value FROM metrics WHERE metric=?
    ORDER BY observed_at DESC LIMIT ?
  `).bind(metric,limit).all();
  return r.results || [];
}

function diff(rows,a,b) {
  if (!rows[a] || !rows[b]) return null;
  return Number((rows[a].value - rows[b].value).toFixed(3));
}
function spreadBp(a,b) {
  if (!a || !b) return null;
  return Number(((a.value-b.value)*100).toFixed(1));
}
function clean(x) {
  if (x === null || x === undefined || x === "null") return null;
  return String(x);
}
function moneyToBn(x) {
  const cleaned = String(x ?? "").replace(/[^0-9+\-.]/g, "");
  const n = Number(cleaned);
  if (!Number.isFinite(n)) return null;
  return n / 1e9;
}
function firstNumber(...xs) {
  for (const x of xs) {
    const n = Number(x);
    if (Number.isFinite(n)) return n;
  }
  return null;
}
function isoDate(d) { return d.toISOString().slice(0,10); }
function json(body,status=200) {
  return new Response(JSON.stringify(body,null,2), {
    status, headers: {"content-type":"application/json; charset=utf-8"}
  });
}

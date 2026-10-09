#!/usr/bin/env python3
"""Additional step in the EXISTING Treasury workflow; one daily snapshot, no new cron.
Requires the existing TREASURY_INGEST_TOKEN. Does not change the original collector.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sox_model import (MODEL_ID, MODEL_VERSION, REFERENCE_END, analyse, asof_fed, input_hash,
                       iso_date, numeric, previous_day, sessions)
from sox_sources import (HttpClient, DataError, TGA_PATH, AUCTIONS_PATH, MSPD_PATH,
                         tga_values, merge_auctions, inventory_maturities, net_public_face, parse_sox_csv)

ROOT = Path(__file__).resolve().parents[1]


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def assemble_daily(tga: dict, net: dict, prices: dict, old_rows: list, *, start: str, end: str,
                   seen_at: str, extra_closed: list[str]) -> list[dict]:
    """Aggregate fiscal flows between adjacent SOX sessions, INCLUDING stock-market-only holidays.
    A Fed holiday can carry its last balance with an explicit as-of date; it is not a fresh DTS value.
    """
    old = {x["date"]: x for x in old_rows if x.get("model_id") == MODEL_ID}
    all_rows = dict(old)
    for day in sessions(start, end, extra_closed=extra_closed):
        prev = previous_day(day, extra_closed=extra_closed)
        tdate, pdate = asof_fed(day), asof_fed(prev)
        needed = sessions((date.fromisoformat(prev)+timedelta(days=1)).isoformat(), day, kind="fed")
        flows = [net.get(d, (None, "no_principal_input")) for d in needed]
        n = sum(x[0] for x in flows) if all(x[0] is not None for x in flows) else None
        oldrow = old.get(day, {})
        t, p = tga.get(tdate), tga.get(pdate)
        statuses = sorted(set(x[1] for x in flows))
        # A newer monthly inventory cannot reconstruct earlier dates. Retain
        # their previously verified inputs, rather than overwriting them with 0.
        if n is None and numeric(oldrow.get("net_issuance_bn")) is not None:
            n = oldrow["net_issuance_bn"]
            statuses = [oldrow.get("net_status", "previous_verified_input")]
        if t is None:
            t = numeric(oldrow.get("tga_bn"))
        if p is None:
            p = numeric(oldrow.get("prior_tga_bn"))
        issues = []
        if t is None or p is None:
            issues.append("TGA_INPUT_MISSING")
        if n is None:
            issues.append("PRINCIPAL_INPUT_MISSING")
        if tdate != day:
            issues.append("FED_CLOSED_BALANCE_ASOF_" + tdate)
        close = prices.get(day, numeric(oldrow.get("sox_close")))
        complete = t is not None and p is not None and n is not None
        row = {"date": day, "model_id": MODEL_ID, "tga_bn": t, "prior_tga_bn": p,
               "net_issuance_bn": n, "sox_close": close, "tga_source_date": tdate,
               "prior_tga_source_date": pdate, "net_status": ";".join(statuses) or "FED_CLOSED_NO_SETTLEMENT",
               "issues": issues, "updated_at": seen_at,
               "first_complete_seen_at": oldrow.get("first_complete_seen_at") or (seen_at if complete else None)}
        row["input_hash"] = input_hash(row)
        all_rows[day] = row
    # Database retention is append-only; this bound is only the analysis window.
    return [all_rows[d] for d in sorted(all_rows)[-500:]]


def collect(*, force: bool = False) -> dict:
    config = json.loads((ROOT / "config/sox-model.json").read_text())
    token = os.environ.get("TREASURY_INGEST_TOKEN", "").strip()
    ingest = os.environ.get("TREASURY_INGEST_URL", "").strip()
    origin = urllib.parse.urlsplit(ingest)
    if origin.scheme != "https" or not origin.hostname or not token:
        raise DataError("existing TREASURY_INGEST_URL/TREASURY_INGEST_TOKEN required")
    base = f"{origin.scheme}://{origin.netloc}"
    http = HttpClient()
    now = datetime.now(timezone.utc)
    ny = now.astimezone(ZoneInfo("America/New_York"))
    stamp, day = now.isoformat(), ny.date().isoformat()
    version = config_hash(config)
    state = http.json(base + "/api/sox/state", token=token)
    if not isinstance(state, dict) or "inputs" not in state:
        raise DataError("SOX Worker/state not deployed or migration missing")
    last = state.get("status") or {}
    changed_config = last.get("config_hash") != version
    # A failed attempt may retain an earlier success timestamp, including one
    # from today. Only a successful latest attempt can suppress the next retry.
    previous_succeeded = last.get("state") in {"READY", "LICENSE_REQUIRED"}
    if not force and not changed_config and previous_succeeded:
        if last.get("last_success_day") == day:
            return {"status": "SKIPPED_ALREADY_UPDATED", "ny_date": day}
        if last.get("last_success_at") and ny.hour < config["update_after_ny_hour"]:
            return {"status": "WAITING_DAILY_WINDOW", "after_hour_et": config["update_after_ny_hour"]}

    warnings, sources = [], {}
    old_rows = state.get("inputs", [])
    cached_auctions = state.get("auctions", [])
    lookback_start = max(REFERENCE_END, (ny.date()-timedelta(days=config["source_lookback_days"])).isoformat())
    try:
        trows, tmeta = http.fiscal(TGA_PATH, {"filter": "record_date:gte:" + lookback_start, "sort": "record_date"})
        tga = tga_values(trows, tmeta)
        latest_tga = min(max(tga), day)
        sources["tga"] = {"status": "ok", "observed_through": latest_tga, "fetched_at": stamp,
                           "source": "Treasury DTS closing balance"}
    except DataError as exc:
        # Preserve last valid report. A failed source check is NOT a zero-input update.
        status = {**last, "last_attempt": stamp, "config_hash": version, "state": "SOURCE_ERROR",
                  "errors": [str(exc)], "warnings": ["Previous data retained; no values set to zero."]}
        http.json(base + "/api/sox/ingest", token=token, body={"status": status})
        raise

    net, inventory_date, inventory_from = {}, None, None
    try:
        # Each historical day uses the latest snapshot strictly before that day.
        # The current snapshot cannot establish July/August maturity schedules.
        anchor = (date.fromisoformat(lookback_start).replace(day=1)-timedelta(days=1)).isoformat()
        mrows, _ = http.fiscal(MSPD_PATH, {
            "filter": f"record_date:gte:{anchor},record_date:lte:{latest_tga}", "sort": "record_date"})
        monthly_rows = {}
        for row in mrows:
            snapshot = iso_date(row.get("record_date"))
            if not snapshot:
                raise DataError("MSPD record date missing")
            monthly_rows.setdefault(snapshot, []).append(row)
        inventories = {snapshot: inventory_maturities(rows) for snapshot, rows in monthly_rows.items()}
        snapshots = sorted(inventories)
        if not snapshots:
            raise DataError("MSPD history missing")
        inventory_from, inventory_date = snapshots[0], snapshots[-1]
        # This full feed validates the issuance universe; D1 alone is not a full calendar.
        # Include issues between the oldest inventory and the analysis start:
        # short bills issued in that gap may mature during the chart's period.
        auction_start = (date.fromisoformat(inventory_from)+timedelta(days=1)).isoformat()
        arows, ameta = http.fiscal(AUCTIONS_PATH, {"filter": "issue_date:gte:" + auction_start, "sort": "issue_date"})
        if not arows:
            raise DataError("empty auctions universe; no zero-day inference allowed")
        auctions = merge_auctions(arows, ameta, cached_auctions)
        for d in sessions(lookback_start, latest_tga, kind="fed"):
            index = bisect_left(snapshots, d)-1
            if index < 0:
                net[d] = (None, "historical_inventory_missing")
                continue
            snapshot = snapshots[index]
            net[d] = net_public_face(d, auctions, inventories[snapshot], snapshot, universe_complete=True)
        sources["principal"] = {"status": "ok", "inventory_asof": inventory_date,
              "inventory_from": inventory_from, "inventory_snapshots": snapshots,
              "auction_rows": len(auctions), "known_days": sum(v[0] is not None for v in net.values()),
              "source": "actual public face less Treasury public maturity estimates", "fetched_at": stamp}
    except (DataError, KeyError, IndexError, ValueError) as exc:
        warnings.append("PRINCIPAL_SOURCE_ERROR: " + str(exc))
        sources["principal"] = {"status": "error", "detail": str(exc)}

    sox_config = config.get("sox", {})
    public_use_confirmed = sox_config.get("public_use_confirmed") is True
    personal_use_confirmed = sox_config.get("personal_use_confirmed") is True
    analysis_allowed = public_use_confirmed or personal_use_confirmed
    analysis_use = "approved_public" if public_use_confirmed else "personal_noncommercial" if personal_use_confirmed else "disabled"
    prices = {}
    if analysis_allowed:
        try:
            provider = config["sox"].get("provider", "fred")
            if provider == "fred":
                url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({
                    "id": "NASDAQSOX", "cosd": lookback_start, "coed": day})
            elif provider == "authorized_csv":
                url = config["sox"].get("csv_url", "")
                if urllib.parse.urlsplit(url).scheme != "https":
                    raise DataError("authorized SOX CSV must use HTTPS")
            else:
                raise DataError("unknown SOX provider")
            last_closed_session = day if ny.hour >= 16 else previous_day(day, extra_closed=config.get("extra_market_closures", []))
            prices = parse_sox_csv(http.request(url).decode("utf-8-sig"), allowed_start=lookback_start, allowed_end=last_closed_session)
            sources["sox"] = {"status": "ok", "observed_through": max(prices), "fetched_at": stamp,
                               "provider": provider, "series": "NASDAQSOX"}
        except (DataError, UnicodeError) as exc:
            warnings.append("SOX_SOURCE_ERROR: " + str(exc))
            sources["sox"] = {"status": "error", "detail": str(exc)}
    else:
        sources["sox"] = {"status": "permission_required", "series": "NASDAQSOX"}
        warnings.append("SOX_PUBLIC_USE_NOT_CONFIRMED")

    extra = config.get("extra_market_closures", [])
    # Backfill the separate daily sample using each month's verified inventory.
    # Dates without a valid prior snapshot/input remain pending, never zero.
    start = max((date.fromisoformat(lookback_start)+timedelta(days=1)).isoformat(),
                (date.fromisoformat(inventory_from)+timedelta(days=1)).isoformat() if inventory_from else lookback_start)
    # Revisit already collected dates to incorporate official revisions where coverage permits.
    if old_rows:
        start = min(start, max(lookback_start, min(r["date"] for r in old_rows)))
    analysis_end = max(latest_tga, max(prices) if prices else latest_tga)
    rows = assemble_daily(tga, net, prices, old_rows, start=start, end=analysis_end,
                          seen_at=stamp, extra_closed=extra)
    # Private revisions retain their audit history. Disabling both permitted
    # uses removes closes from the analysis without inventing missing values.
    analysis_rows = rows if analysis_allowed else [{**r, "sox_close": None} for r in rows]
    analysis = analyse(analysis_rows, extra_closed=extra)
    analysis.pop("daily", None)
    old_hashes = {r["date"]: r.get("input_hash") for r in old_rows}
    changed = [r for r in rows if old_hashes.get(r["date"]) != r.get("input_hash")]
    report = {"schema_version": 1, "module_version": MODEL_VERSION, "model_id": MODEL_ID,
              "generated_at": stamp, "analysis_from": rows[0]["date"] if rows else None,
              "analysis_through": rows[-1]["date"] if rows else None,
              "public_use_confirmed": public_use_confirmed,
              "personal_use_confirmed": personal_use_confirmed,
              "analysis_use": analysis_use,
              "selection": "ALL_ELIGIBLE_SESSIONS_NO_OUTCOME_FILTER", "sources": sources,
              "warnings": warnings, "data_days": len(rows), **analysis}
    had_source_error = any(s.get("status") == "error" for s in sources.values())
    status = {"last_attempt": stamp, "last_success_day": day if not had_source_error and ny.hour >= config["update_after_ny_hour"] else last.get("last_success_day"),
              "last_success_at": stamp if not had_source_error else last.get("last_success_at"),
              "config_hash": version, "state": "PARTIAL" if had_source_error else
                    "LICENSE_REQUIRED" if not analysis_allowed else "READY",
              "warnings": warnings, "source_dates": {k: v.get("observed_through", v.get("inventory_asof")) for k,v in sources.items()}}
    # Small idempotent D1 batches. Do not publish a new report until all rows succeeded.
    for i in range(0, len(changed), 25):
        answer = http.json(base + "/api/sox/ingest", token=token, body={"inputs": changed[i:i+25]})
        if answer.get("ok") is not True:
            raise DataError("SOX input ingest failed")
    answer = http.json(base + "/api/sox/ingest", token=token, body={"report": report, "status": status})
    if answer.get("ok") is not True:
        raise DataError("SOX report ingest failed")
    return {"status": status["state"], "changed_input_days": len(changed), "stored_days": len(rows),
            "drawdown_points": analysis["stats"]["drawdown"]["n"], "rebound_points": analysis["stats"]["rebound"]["n"],
            "latest_macro_date": (analysis.get("latest_macro") or {}).get("date"), "sources": sources, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore once-per-day gate")
    args = parser.parse_args()
    try:
        result = collect(force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if result.get("status") == "LICENSE_REQUIRED":
            print("::notice::SOX collection/analysis is disabled until personal noncommercial use or public data use is confirmed; Treasury inputs still update.")
        if result.get("status") == "PARTIAL":
            print("::warning::Some SOX research inputs are unavailable; inspect source status, never treat missing inputs as zero.")
        # Partial data remain visible as partial; a source error must not masquerade as a full green success.
        return 1 if result.get("status") == "PARTIAL" else 0
    except (DataError, OSError, ValueError) as exc:
        print("::error::SOX daily update: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

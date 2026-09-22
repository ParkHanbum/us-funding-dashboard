#!/usr/bin/env python3
import json
import os
import sys
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

AUCTIONED_URL = (
    "https://www.treasurydirect.gov/TA_WS/securities/"
    "auctioned?format=json&day=45"
)
ANNOUNCED_URL = (
    "https://www.treasurydirect.gov/TA_WS/securities/"
    "announced?format=json&days=45"
)
FISCALDATA_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/accounting/od/auctions_query?sort=-auction_date&page%5Bsize%5D=250"
)

INGEST_URL = os.environ.get("TREASURY_INGEST_URL", "").strip()
INGEST_TOKEN = os.environ.get("TREASURY_INGEST_TOKEN", "").strip()

POST_CHUNK_SIZE = 20
POST_TIMEOUT = 90
POST_RETRIES = 3


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.2",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def post_json_once(url, payload, token):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.2",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=POST_TIMEOUT) as resp:
        return resp.status, json.load(resp)


def post_json_retry(url, payload, token):
    last = None
    for attempt in range(1, POST_RETRIES + 1):
        try:
            return post_json_once(url, payload, token)
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            last = e
            if attempt == POST_RETRIES:
                raise
            time.sleep(2 ** (attempt - 1))
    raise last


def date_only(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def number(value):
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s or s.lower() == "null":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def td_normalize(row, status):
    return {
        "cusip": row.get("cusip"),
        "security_type": row.get("securityType"),
        "security_term": row.get("securityTerm"),
        "auction_date": date_only(row.get("auctionDate")),
        "issue_date": date_only(row.get("issueDate")),
        "maturity_date": date_only(row.get("maturityDate")),
        "offering_amt": number(row.get("offeringAmount")),
        "total_accepted": number(row.get("totalAccepted")),
        "soma_accepted": number(row.get("somaAccepted")),
        "price_per_100": number(
            row.get("unadjustedPrice")
            or row.get("pricePer100")
            or row.get("highPrice")
        ),
        "status": status,
    }


def first(row, *keys):
    for key in keys:
        v = row.get(key)
        if v is not None and str(v).strip().lower() not in ("", "null"):
            return v
    return None


def fiscal_normalize(row):
    accepted = number(first(row, "total_accepted", "total_accepted_amt"))
    offering = number(first(row, "offering_amt", "offering_amount"))
    soma = number(first(row, "soma_accepted", "soma_accepted_amt"))

    return {
        "cusip": first(row, "cusip"),
        "security_type": first(row, "security_type"),
        "security_term": first(row, "security_term"),
        "auction_date": date_only(first(row, "auction_date", "record_date")),
        "issue_date": date_only(first(row, "issue_date")),
        "maturity_date": date_only(first(row, "maturity_date")),
        "offering_amt": offering,
        "total_accepted": accepted,
        "soma_accepted": soma,
        "price_per_100": number(first(
            row, "unadj_price", "price_per100", "price_per_100", "high_price"
        )),
        "adjusted_price": number(first(row, "adj_price", "adjusted_price")),
        "accrued_interest_per_100": number(first(
            row, "accrued_int", "accrued_interest_per_100",
            "unadj_accrued_int", "unadjusted_accrued_interest_per_100"
        )),
        "adjusted_accrued_interest_per_100": number(first(
            row, "adj_accrued_int", "adjusted_accrued_interest_per_100"
        )),
        "maturing_date": date_only(first(row, "mat_date", "maturing_date")),
        "est_pub_held_mat_by_type_amt": number(first(
            row, "est_pub_held_mat_by_type_amt"
        )),
        "soma_holdings_maturing": number(first(
            row, "soma_holdings", "soma_holdings_maturing"
        )),
        "reopening": first(row, "reopening"),
        "status": "actual" if accepted is not None and accepted > 0 else "tentative",
    }


def merge_rows(primary, extra):
    out = dict(primary)
    for k, v in extra.items():
        if v is not None:
            # TreasuryDirect result status wins once completed.
            if k == "status" and out.get("status") == "actual":
                continue
            out[k] = v
    return out


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i+n]


def main():
    if not INGEST_URL:
        print("TREASURY_INGEST_URL is missing", file=sys.stderr)
        return 2
    if not INGEST_TOKEN:
        print("TREASURY_INGEST_TOKEN is missing", file=sys.stderr)
        return 2

    auctioned = fetch_json(AUCTIONED_URL)
    announced = fetch_json(ANNOUNCED_URL)

    dedup = {}
    for item in auctioned if isinstance(auctioned, list) else []:
        row = td_normalize(item, "actual")
        if row["cusip"] and row["auction_date"]:
            dedup[(row["cusip"], row["auction_date"])] = row

    for item in announced if isinstance(announced, list) else []:
        row = td_normalize(item, "tentative")
        if not row["cusip"] or not row["auction_date"]:
            continue
        key = (row["cusip"], row["auction_date"])
        old = dedup.get(key)
        if old is None or old.get("status") != "actual":
            dedup[key] = row

    fiscal_count = 0
    fiscal_error = None
    try:
        body = fetch_json(FISCALDATA_URL)
        fiscal_rows = body.get("data", []) if isinstance(body, dict) else []
        fiscal_count = len(fiscal_rows)

        for item in fiscal_rows:
            rich = fiscal_normalize(item)
            if not rich["cusip"] or not rich["auction_date"]:
                continue
            key = (rich["cusip"], rich["auction_date"])
            if key in dedup:
                dedup[key] = merge_rows(dedup[key], rich)
            else:
                dedup[key] = rich
    except Exception as e:
        # Core collection remains usable even if FiscalData enrichment is down.
        fiscal_error = str(e)

    all_rows = list(dedup.values())
    total_accepted = 0
    total_skipped = 0
    responses = []

    for idx, batch in enumerate(chunks(all_rows, POST_CHUNK_SIZE), start=1):
        payload = {
            "source": "github-actions-treasury",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "batch": idx,
            "auctions": batch,
        }
        status, result = post_json_retry(INGEST_URL, payload, INGEST_TOKEN)
        responses.append({"batch": idx, "status": status, "result": result})
        total_accepted += int(result.get("accepted", 0))
        total_skipped += int(result.get("skipped", 0))

    print(json.dumps({
        "treasurydirect_auctioned_rows":
            len(auctioned) if isinstance(auctioned, list) else 0,
        "treasurydirect_announced_rows":
            len(announced) if isinstance(announced, list) else 0,
        "fiscaldata_rows": fiscal_count,
        "fiscaldata_error": fiscal_error,
        "dedup_rows": len(all_rows),
        "batches": len(responses),
        "accepted": total_accepted,
        "skipped": total_skipped,
        "responses": responses,
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
import json
import os
import sys
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

INGEST_URL = os.environ.get("TREASURY_INGEST_URL", "").strip()
INGEST_TOKEN = os.environ.get("TREASURY_INGEST_TOKEN", "").strip()


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def post_json(url, payload, token):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.load(resp)


def date_only(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None

    # TreasuryDirect usually emits ISO timestamp/date.
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]

    # Defensive US date handling.
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


def normalize(row, status):
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
        "raw": row,
    }


def main():
    if not INGEST_URL:
        print("TREASURY_INGEST_URL is missing", file=sys.stderr)
        return 2
    if not INGEST_TOKEN:
        print("TREASURY_INGEST_TOKEN is missing", file=sys.stderr)
        return 2

    auctioned = fetch_json(AUCTIONED_URL)
    announced = fetch_json(ANNOUNCED_URL)

    rows = []
    for item in auctioned if isinstance(auctioned, list) else []:
        rows.append(normalize(item, "actual"))
    for item in announced if isinstance(announced, list) else []:
        rows.append(normalize(item, "tentative"))

    # Deduplicate locally. Completed results win over announcements.
    dedup = {}
    for row in rows:
        if not row["cusip"] or not row["auction_date"]:
            continue
        key = (row["cusip"], row["auction_date"])
        old = dedup.get(key)
        if old is None or row["status"] == "actual":
            dedup[key] = row

    payload = {
        "source": "github-actions-treasurydirect",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "auctions": list(dedup.values()),
    }

    status, result = post_json(INGEST_URL, payload, INGEST_TOKEN)
    print(json.dumps({
        "http_status": status,
        "auctioned_rows": len(auctioned) if isinstance(auctioned, list) else 0,
        "announced_rows": len(announced) if isinstance(announced, list) else 0,
        "sent_rows": len(payload["auctions"]),
        "worker_result": result,
    }, indent=2))

    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())

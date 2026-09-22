#!/usr/bin/env python3
import json
import os
import sys
import time
import socket
import urllib.error
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

POST_CHUNK_SIZE = 20
POST_TIMEOUT = 90
POST_RETRIES = 3


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.1",
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
            "User-Agent": "us-funding-dashboard-github-actions/1.1",
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


def normalize(row, status):
    # IMPORTANT: don't send the whole raw TreasuryDirect row.
    # Keep payload small and D1 writes fast.
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

    rows = []
    for item in auctioned if isinstance(auctioned, list) else []:
        rows.append(normalize(item, "actual"))
    for item in announced if isinstance(announced, list) else []:
        rows.append(normalize(item, "tentative"))

    dedup = {}
    for row in rows:
        if not row["cusip"] or not row["auction_date"]:
            continue
        key = (row["cusip"], row["auction_date"])
        old = dedup.get(key)
        if old is None or row["status"] == "actual":
            dedup[key] = row

    all_rows = list(dedup.values())
    total_accepted = 0
    total_skipped = 0
    responses = []

    for idx, batch in enumerate(chunks(all_rows, POST_CHUNK_SIZE), start=1):
        payload = {
            "source": "github-actions-treasurydirect",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "batch": idx,
            "auctions": batch,
        }
        status, result = post_json_retry(INGEST_URL, payload, INGEST_TOKEN)
        responses.append({"batch": idx, "status": status, "result": result})
        total_accepted += int(result.get("accepted", 0))
        total_skipped += int(result.get("skipped", 0))

    print(json.dumps({
        "auctioned_rows": len(auctioned) if isinstance(auctioned, list) else 0,
        "announced_rows": len(announced) if isinstance(announced, list) else 0,
        "dedup_rows": len(all_rows),
        "batches": len(responses),
        "accepted": total_accepted,
        "skipped": total_skipped,
        "responses": responses,
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

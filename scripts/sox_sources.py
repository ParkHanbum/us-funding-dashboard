"""Strict public-data adapters. Missing values, incomplete pages and conflicting maturities fail closed."""
from __future__ import annotations

import csv
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any
from sox_model import numeric, iso_date, sessions, asof_fed, previous_day, business_day, MODEL_ID

FD_ROOT = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
TGA_PATH = "/v1/accounting/dts/operating_cash_balance"
AUCTIONS_PATH = "/v1/accounting/od/auctions_query"
MSPD_PATH = "/v1/debt/mspd/mspd_table_3_market"


class DataError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, deadline_seconds: float = 165):
        self.deadline = time.monotonic() + deadline_seconds

    def request(self, url: str, *, body: dict | None = None, token: str | None = None) -> bytes:
        # Tokens are never sent on redirects or to arbitrary upstream hosts.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                raise DataError("redirect rejected for authenticated request")
        headers = {"Accept": "application/json,text/csv", "User-Agent": "funding-dashboard-sox/3.3"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
        req = urllib.request.Request(url, data=data, headers=headers)
        for attempt in range(2):
            remaining = self.deadline - time.monotonic()
            if remaining < 3:
                raise DataError("collector time budget exhausted")
            try:
                opener = urllib.request.build_opener(NoRedirect()) if token else urllib.request.build_opener()
                with opener.open(req, timeout=min(35, remaining)) as response:
                    b = response.read(12_000_001)
                    if len(b) > 12_000_000:
                        raise DataError("response size limit exceeded")
                    return b
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504, 520, 525} or attempt:
                    raise DataError(f"HTTP {exc.code} from {urllib.parse.urlparse(url).hostname}") from None
            except (OSError, urllib.error.URLError) as exc:
                if attempt:
                    raise DataError(f"network failure: {type(exc).__name__} at {urllib.parse.urlparse(url).hostname}") from None
            time.sleep(1)
        raise DataError("request failed")

    def json(self, url: str, **kwargs) -> Any:
        try:
            return json.loads(self.request(url, **kwargs))
        except (json.JSONDecodeError, UnicodeError):
            raise DataError("non-JSON upstream response") from None

    def fiscal(self, path: str, params: dict, *, limit: int = 12000) -> tuple[list[dict], dict]:
        result, meta, expected = [], {}, None
        for page in range(1, 25):
            query = {**params, "page[size]": 1000, "page[number]": page}
            body = self.json(FD_ROOT + path + "?" + urllib.parse.urlencode(query))
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise DataError(f"FiscalData invalid shape: {path}")
            chunk, meta = body["data"], body.get("meta", {})
            if page == 1:
                expected = meta.get("total-count")
                if expected is not None:
                    expected = int(expected)
                    if expected > limit:
                        raise DataError(f"FiscalData exceeds row budget ({expected})")
            result.extend(chunk)
            if len(result) > limit:
                raise DataError("FiscalData truncated: too many rows")
            has_next = bool(body.get("links", {}).get("next"))
            if not has_next and (expected is None or len(result) >= expected):
                return result, meta
            if not chunk:
                raise DataError("FiscalData pagination incomplete")
        raise DataError("FiscalData pagination limit exceeded")


def pick(row: dict, *keys: str):
    for key in keys:
        if row.get(key) is not None and str(row[key]).strip().lower() not in {"", "null", "."}:
            return row[key]
    return None


def dollar_field(row: dict, meta: dict, *keys: str) -> float | None:
    for key in keys:
        n = numeric(row.get(key))
        if n is None:
            continue
        fmt = meta.get("dataFormats", {}).get(key)
        # These auction amount fields use USD. FiscalData dataFormats are
        # display examples, not unit multipliers: offering_amt=58000000000
        # has format "$1,000,000", while accepted amounts use "10.2".
        # DTS balances use a different, explicitly defined unit in tga_values.
        if fmt is None or fmt in {"10.2", "10.0", "$1,000,000", "$1", "$1.00", "$1.000000", "$1.000", "$1.0000", "$1.00000", "$1.00000000", "$1.0000000"}:
            return n
        raise DataError(f"unrecognized auction amount format {key}={fmt}")
    return None


def normalize_auction(row: dict, meta: dict | None = None, *, canonical: bool = False) -> dict:
    meta = meta or {}
    getmoney = (lambda *keys: numeric(pick(row, *keys))) if canonical else (lambda *keys: dollar_field(row, meta, *keys))
    d = {
        "cusip": pick(row, "cusip"),
        "auction_date": iso_date(pick(row, "auction_date", "auctionDate")),
        "issue_date": iso_date(pick(row, "issue_date", "issueDate")),
        "maturity_date": iso_date(pick(row, "maturity_date", "maturityDate")),
        "bucket": "bill" if "bill" in str(pick(row, "security_type", "securityType") or "").lower() else "coupon",
        "accepted": getmoney("total_accepted", "total_accepted_amt", "totalAccepted"),
        "soma": getmoney("soma_accepted", "soma_accepted_amt", "somaAccepted"),
        "offering": getmoney("offering_amt", "offering_amount", "offeringAmount"),
        "maturing_date": iso_date(pick(row, "maturing_date", "mat_date", "maturingDate")),
        "public_maturing": getmoney("est_pub_held_mat_by_type_amt", "estimatedPubliclyHeldMaturingSecurities"),
    }
    return d


def merge_auctions(fd: list[dict], meta: dict, cache: list[dict]) -> list[dict]:
    bykey = {}
    for row in fd:
        d = normalize_auction(row, meta)
        if not d["cusip"] or not d["issue_date"] or not d["auction_date"]:
            raise DataError("auction feed contains missing identifiers/date")
        bykey[(d["cusip"], d["auction_date"])] = d
    # Existing Actions already preserves verified announcement-only maturity fields.
    for stored in cache:
        raw = stored.get("raw_json")
        try:
            raw = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        d = normalize_auction(raw, canonical=True)  # collector contract: raw amounts = USD
        key = (d["cusip"], d["auction_date"])
        old = bykey.get(key)
        # Cached rows must not broaden an otherwise complete official feed.
        if old is None:
            continue
        for k in ("accepted", "soma", "offering"):
            if old[k] is None and d[k] is not None:
                old[k] = d[k]
        if raw.get("maturity_source") == "treasurydirect-announcement":
            if d["public_maturing"] is not None and d["maturing_date"]:
                old["public_maturing"] = d["public_maturing"]
                old["maturing_date"] = d["maturing_date"]
    return list(bykey.values())


def tga_values(rows: list[dict], meta: dict) -> dict[str, float]:
    result = {}
    for r in rows:
        if r.get("account_type") != "Treasury General Account (TGA) Closing Balance":
            continue
        day, n = iso_date(r.get("record_date")), numeric(r.get("open_today_bal"))
        if not day or n is None:
            raise DataError("TGA closing balance missing")
        fmt = meta.get("dataFormats", {}).get("open_today_bal")
        if fmt not in {"$1,000,000", None}:
            raise DataError(f"TGA display format changed: {fmt}")
        result[day] = n / 1000  # DTS amounts in millions -> USD bn
    if not result:
        raise DataError("no TGA closing-balance rows")
    return result


def inventory_maturities(rows: list[dict]) -> dict[str, set[str]]:
    dates, count = {}, 0
    for r in rows:
        cls = str(pick(r, "security_class1_desc", "security_class_desc") or "").lower()
        day = iso_date(r.get("maturity_date"))
        if not day:
            continue  # aggregate / total rows have no maturity date
        count += 1
        bucket = "bill" if "bill" in cls else "coupon"
        # Treasury payments move forward to a Federal Reserve business day.
        d = date.fromisoformat(day)
        while not business_day(d, "fed"):
            d += timedelta(days=1)
        dates.setdefault(d.isoformat(), set()).add(bucket)
    if count < 10:
        raise DataError("MSPD maturity inventory absent/incomplete")
    return dates


def net_public_face(day: str, auctions: list[dict], inventory: dict[str, set[str]], snapshot_date: str,
                    universe_complete: bool) -> tuple[float | None, str]:
    """Announcement maturity is an aggregate by bills/coupons, not per CUSIP.
    No-auction day != zero: zero additionally needs a complete post-snapshot maturity inventory.
    N is a FACE proxy; coupon payments, buybacks, TIPS revaluation are NOT mixed into this model.
    """
    rows = [r for r in auctions if r.get("issue_date") == day]
    if not universe_complete:
        return None, "auction_coverage_incomplete"
    required = set(inventory.get(day, set()))
    for r in auctions:
        mat = r.get("maturity_date")
        if mat:
            d = date.fromisoformat(mat)
            while not business_day(d, "fed"):
                d += timedelta(days=1)
            if d.isoformat() == day:
                required.add(r["bucket"])
    # Keep the adjusted payment dates when deciding whether a missing maturity
    # estimate can be a proven zero, including securities issued after MSPD.
    scheduled_maturities = set(required)
    if not rows:
        if day > snapshot_date and not required:
            return 0.0, "inventory_verified_no_scheduled_principal"
        return None, "maturity_without_issuance_or_before_snapshot"
    if day <= snapshot_date:
        return None, "before_current_inventory_snapshot"
    issuance, maturities = 0.0, {}
    for r in rows:
        total, soma = r["accepted"], r["soma"]
        if total is None or soma is None:
            return None, "actual_acceptance_or_soma_missing"
        if total < soma or total <= 0:
            return None, "invalid_public_acceptance"
        issuance += total - soma
        required.add(r["bucket"])
        n, mdate = r.get("public_maturing"), r.get("maturing_date")
        if mdate:
            payment = date.fromisoformat(mdate)
            while not business_day(payment, "fed"):
                payment += timedelta(days=1)
            mdate = payment.isoformat()
        if n is not None and mdate == day:
            if n < 0:
                return None, "negative_maturity"
            previous = maturities.get(r["bucket"])
            # Don't cherry-pick the largest among contradictory announcements.
            if previous is not None and abs(previous - n) > 1.0:
                return None, "conflicting_maturity_aggregates"
            maturities[r["bucket"]] = n
    for bucket in required:
        if bucket not in maturities:
            # Only permit a proven no-maturity bucket, not a missing estimate as 0.
            if bucket not in scheduled_maturities:
                maturities[bucket] = 0.0
            else:
                return None, "public_maturity_missing"
    return (issuance - sum(maturities.values())) / 1e9, "public_face_minus_official_maturity"


def parse_sox_csv(text: str, *, allowed_start: str, allowed_end: str) -> dict[str, float]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    names = set(reader.fieldnames or [])
    daykey = "observation_date" if "observation_date" in names else "DATE" if "DATE" in names else "date"
    valuekey = "NASDAQSOX" if "NASDAQSOX" in names else "close"
    if daykey not in names or valuekey not in names:
        raise DataError("SOX CSV requires DATE/observation_date + NASDAQSOX, or date + close")
    out = {}
    for row in reader:
        day, n = iso_date(row.get(daykey)), numeric(row.get(valuekey))
        if day and allowed_start <= day <= allowed_end and n is not None:
            if n <= 0:
                raise DataError("invalid SOX close")
            if day in out and out[day] != n:
                raise DataError("conflicting SOX closes")
            out[day] = n
    if not out:
        raise DataError("no valid SOX observations; never fill missing close with zero")
    return out

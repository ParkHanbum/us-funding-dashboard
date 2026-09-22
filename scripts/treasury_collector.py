#!/usr/bin/env python3
import calendar
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
MSPD_BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/debt/mspd/mspd_table_3_market"
)
NYFED_SOMA_LATEST = (
    "https://markets.newyorkfed.org/api/soma/asofdates/latest.json"
)
NYFED_SOMA_BY_DATE = (
    "https://markets.newyorkfed.org/api/soma/tsy/get/all/asof/{date}.json"
)

INGEST_URL = os.environ.get("TREASURY_INGEST_URL", "").strip()
INGEST_TOKEN = os.environ.get("TREASURY_INGEST_TOKEN", "").strip()

POST_CHUNK_SIZE = 20
POST_TIMEOUT = 90
POST_RETRIES = 3
COUPON_HORIZON_DAYS = 21


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.3",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def post_json_once(url, payload, token):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "us-funding-dashboard-github-actions/1.3",
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
        except (
            TimeoutError,
            socket.timeout,
            urllib.error.URLError,
        ) as e:
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
    s = (
        str(value)
        .replace(",", "")
        .replace("$", "")
        .replace("%", "")
        .strip()
    )
    if not s or s.lower() in ("null", "none", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalized_key(key):
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def find_value_by_key(row, predicate):
    for key, value in row.items():
        if value is None:
            continue
        if predicate(normalized_key(key)):
            return value
    return None


def find_exactish(row, *names):
    wanted = {normalized_key(x) for x in names}
    for key, value in row.items():
        if normalized_key(key) in wanted and value not in (None, ""):
            return value
    return None


def td_maturity_metadata(row):
    amount_raw = find_value_by_key(
        row,
        lambda k: (
            "maturing" in k
            and "held" in k
            and ("pub" in k or "public" in k)
            and ("est" in k or "estimated" in k)
        ),
    )
    date_raw = find_value_by_key(
        row,
        lambda k: "maturing" in k and "date" in k,
    )
    soma_raw = find_value_by_key(
        row,
        lambda k: "soma" in k and "maturing" in k,
    )

    return {
        "maturing_date": date_only(date_raw),
        "est_pub_held_mat_by_type_amt": number(amount_raw),
        "soma_holdings_maturing": number(soma_raw),
    }


def td_cash_metadata(row, security_type):
    st = str(security_type or "").lower()
    tips = "tips" in st or str(find_exactish(row, "tips") or "").lower() in (
        "yes", "true", "1"
    )

    adjusted_price = number(find_exactish(
        row, "adjustedPrice", "adjPrice"
    ))
    unadjusted_price = number(find_exactish(
        row, "unadjustedPrice", "pricePer100", "highPrice"
    ))

    accrued100 = number(find_exactish(
        row, "accruedInterestPer100"
    ))
    adjusted_accrued1000 = number(find_exactish(
        row,
        "adjustedAccruedInterestPer1000",
        "adjustedAccruedInterestPayablePer1000",
    ))
    unadjusted_accrued1000 = number(find_exactish(
        row, "unadjustedAccruedInterestPer1000"
    ))
    generic_accrued1000 = number(find_exactish(
        row, "accruedInterestPer1000"
    ))

    if tips:
        cash_price = adjusted_price or unadjusted_price
        if adjusted_accrued1000 is not None:
            cash_accrued100 = adjusted_accrued1000 / 10.0
        elif accrued100 is not None:
            cash_accrued100 = accrued100
        elif generic_accrued1000 is not None:
            cash_accrued100 = generic_accrued1000 / 10.0
        else:
            cash_accrued100 = 0.0
        basis = "tips-adjusted"
    else:
        cash_price = unadjusted_price or adjusted_price
        if accrued100 is not None:
            cash_accrued100 = accrued100
        elif unadjusted_accrued1000 is not None:
            cash_accrued100 = unadjusted_accrued1000 / 10.0
        elif generic_accrued1000 is not None:
            cash_accrued100 = generic_accrued1000 / 10.0
        else:
            cash_accrued100 = 0.0
        basis = "nominal"

    return {
        "cash_price_per_100": cash_price,
        "cash_accrued_interest_per_100": cash_accrued100,
        "cash_basis": basis,
        "tips": tips,
        "index_ratio_on_issue_date": number(find_exactish(
            row, "indexRatioOnIssueDate", "indexRatio"
        )),
        "interest_rate_pct": number(find_exactish(
            row, "interestRate", "interestRatePct"
        )),
        "first_interest_payment_date": date_only(find_exactish(
            row, "firstInterestPaymentDate"
        )),
    }


def td_normalize(row, status):
    security_type = row.get("securityType")
    maturity = td_maturity_metadata(row)
    cash = td_cash_metadata(row, security_type)

    result = {
        "cusip": row.get("cusip"),
        "security_type": security_type,
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
        **maturity,
        **cash,
    }

    if maturity["est_pub_held_mat_by_type_amt"] is not None:
        result["maturity_source"] = "treasurydirect-announcement"

    return result


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

    security_type = first(row, "security_type")
    tips = "tips" in str(security_type or "").lower()

    unadj_price = number(first(
        row, "unadj_price", "price_per100", "price_per_100", "high_price"
    ))
    adj_price = number(first(row, "adj_price", "adjusted_price"))

    # FiscalData data dictionary names have varied by vintage. Canonicalize
    # to per-$100 here; TreasuryDirect values take precedence after merge.
    accrued100 = number(first(row, "accrued_interest_per_100"))
    unadj_ai1000 = number(first(
        row, "unadj_accrued_int", "unadjusted_accrued_interest_per_1000"
    ))
    adj_ai1000 = number(first(
        row, "adj_accrued_int", "adjusted_accrued_interest_per_1000"
    ))

    cash_price = (adj_price or unadj_price) if tips else (unadj_price or adj_price)
    if tips and adj_ai1000 is not None:
        cash_ai100 = adj_ai1000 / 10.0
    elif accrued100 is not None:
        cash_ai100 = accrued100
    elif unadj_ai1000 is not None:
        cash_ai100 = unadj_ai1000 / 10.0
    else:
        cash_ai100 = 0.0

    return {
        "cusip": first(row, "cusip"),
        "security_type": security_type,
        "security_term": first(row, "security_term"),
        "auction_date": date_only(first(row, "auction_date", "record_date")),
        "issue_date": date_only(first(row, "issue_date")),
        "maturity_date": date_only(first(row, "maturity_date")),
        "offering_amt": offering,
        "total_accepted": accepted,
        "soma_accepted": soma,
        "price_per_100": unadj_price,
        "adjusted_price": adj_price,
        "cash_price_per_100": cash_price,
        "cash_accrued_interest_per_100": cash_ai100,
        "cash_basis": "tips-adjusted" if tips else "nominal",
        "tips": tips,
        "index_ratio_on_issue_date": number(first(
            row, "index_ratio_on_issue_date", "index_ratio"
        )),
        "interest_rate_pct": number(first(row, "interest_rate", "interest_rate_pct")),
        "maturing_date": date_only(first(row, "mat_date", "maturing_date")),
        "est_pub_held_mat_by_type_amt": number(first(
            row, "est_pub_held_mat_by_type_amt"
        )),
        "soma_holdings_maturing": number(first(
            row, "soma_holdings", "soma_holdings_maturing"
        )),
        "maturity_source": (
            "fiscaldata"
            if first(row, "est_pub_held_mat_by_type_amt") is not None
            else None
        ),
        "reopening": first(row, "reopening"),
        "status": "actual" if accepted is not None and accepted > 0 else "tentative",
    }


def merge_rows(primary, extra):
    out = dict(primary)
    prefer_existing = {
        "maturing_date",
        "est_pub_held_mat_by_type_amt",
        "soma_holdings_maturing",
        "maturity_source",
        "cash_price_per_100",
        "cash_accrued_interest_per_100",
        "cash_basis",
        "index_ratio_on_issue_date",
    }

    for k, v in extra.items():
        if v is None:
            continue

        if k in prefer_existing and out.get(k) is not None:
            continue

        if k == "status" and out.get("status") == "actual":
            continue

        out[k] = v
    return out


def merge_announcement_into_result(result_row, announcement_row):
    out = dict(result_row)

    for key in (
        "maturing_date",
        "est_pub_held_mat_by_type_amt",
        "soma_holdings_maturing",
        "maturity_source",
    ):
        value = announcement_row.get(key)
        if value is not None:
            out[key] = value

    if out.get("offering_amt") is None and announcement_row.get("offering_amt") is not None:
        out["offering_amt"] = announcement_row["offering_amt"]

    return out


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i+n]


# ---------------------------------------------------------------------
# Coupon schedule
# ---------------------------------------------------------------------

def nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta + 7 * (n - 1))


def last_weekday(year, month, weekday):
    d = date(year, month, calendar.monthrange(year, month)[1])
    delta = (d.weekday() - weekday) % 7
    return d - timedelta(days=delta)


def observed_fixed(year, month, day):
    d = date(year, month, day)
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def fed_holidays(year):
    holidays = {
        observed_fixed(year, 1, 1),
        nth_weekday(year, 1, 0, 3),   # MLK
        nth_weekday(year, 2, 0, 3),   # Washington's Birthday
        last_weekday(year, 5, 0),     # Memorial
        observed_fixed(year, 6, 19),  # Juneteenth
        observed_fixed(year, 7, 4),   # Independence
        nth_weekday(year, 9, 0, 1),   # Labor
        nth_weekday(year, 10, 0, 2),  # Columbus
        observed_fixed(year, 11, 11), # Veterans
        nth_weekday(year, 11, 3, 4),  # Thanksgiving
        observed_fixed(year, 12, 25), # Christmas
    }

    # A Saturday Jan 1 can be observed on Dec 31 of the previous year.
    next_new_year_obs = observed_fixed(year + 1, 1, 1)
    if next_new_year_obs.year == year:
        holidays.add(next_new_year_obs)

    return holidays


def next_fed_business_day(d):
    holidays = fed_holidays(d.year) | fed_holidays(d.year + 1)
    while d.weekday() >= 5 or d in holidays:
        d += timedelta(days=1)
    return d


def fixed_coupon_scheduled_on(d, maturity):
    if d.day != maturity.day:
        return False
    months = (maturity.year - d.year) * 12 + (maturity.month - d.month)
    return months >= 0 and months % 6 == 0


def coupon_payment_dates(maturity, start, end):
    # Check a few days before the requested range because a scheduled
    # weekend/holiday payment can roll into the range.
    scan_start = start - timedelta(days=4)
    d = scan_start
    out = set()

    while d <= end:
        if fixed_coupon_scheduled_on(d, maturity):
            paid = next_fed_business_day(d)
            if start <= paid <= end:
                out.add(paid)
        d += timedelta(days=1)

    return sorted(out)


def parse_latest_soma_date(body):
    candidates = []

    def walk(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            d = date_only(obj)
            if d:
                candidates.append(d)

    walk(body)
    return max(candidates) if candidates else None


def extract_soma_holdings(body):
    if not isinstance(body, dict):
        return []
    soma = body.get("soma")
    if isinstance(soma, dict) and isinstance(soma.get("holdings"), list):
        return soma["holdings"]

    # Defensive recursive fallback.
    stack = [body]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("holdings"), list):
                return x["holdings"]
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return []


def first_semantic(row, *needles):
    for key, value in row.items():
        nk = normalized_key(key)
        if all(n in nk for n in needles):
            return value
    return None


def mspd_cusip(row):
    return str(
        first(row, "cusip", "cusip_number", "cusip_nbr")
        or first_semantic(row, "cusip")
        or ""
    ).strip()


def mspd_class(row):
    return str(
        first(row, "security_class1_desc", "security_class_desc")
        or first_semantic(row, "security", "class")
        or ""
    ).strip()


def build_coupon_schedule():
    latest_url = (
        MSPD_BASE
        + "?fields=record_date&sort=-record_date&page%5Bsize%5D=1"
    )
    latest_body = fetch_json(latest_url)
    latest_rows = latest_body.get("data", []) if isinstance(latest_body, dict) else []
    if not latest_rows:
        raise RuntimeError("MSPD latest record date missing")

    mspd_date = date_only(latest_rows[0].get("record_date"))
    if not mspd_date:
        raise RuntimeError("MSPD latest record date invalid")

    query = urllib.parse.urlencode({
        "filter": f"record_date:eq:{mspd_date}",
        "page[size]": "10000",
    })
    mspd_body = fetch_json(MSPD_BASE + "?" + query)
    mspd_rows = mspd_body.get("data", []) if isinstance(mspd_body, dict) else []

    soma_latest_body = fetch_json(NYFED_SOMA_LATEST)
    soma_as_of = parse_latest_soma_date(soma_latest_body)
    if not soma_as_of:
        raise RuntimeError("NY Fed SOMA latest as-of date missing")

    soma_body = fetch_json(NYFED_SOMA_BY_DATE.format(date=soma_as_of))
    soma_rows = extract_soma_holdings(soma_body)

    soma_by_cusip = {}
    for row in soma_rows:
        cusip = str(
            row.get("cusip")
            or first_semantic(row, "cusip")
            or ""
        ).strip()
        if not cusip:
            continue

        par = number(
            row.get("parValue")
            or row.get("par_value")
            or first_semantic(row, "par", "value")
        ) or 0.0
        inflation = number(
            row.get("inflationCompensation")
            or row.get("inflation_compensation")
            or first_semantic(row, "inflation", "compensation")
        ) or 0.0

        soma_by_cusip[cusip] = {
            "par_usd": par,
            "inflation_usd": inflation,
        }

    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    start = today_ny
    end = start + timedelta(days=COUPON_HORIZON_DAYS)

    schedule = {
        (start + timedelta(days=i)).isoformat(): {
            "date": (start + timedelta(days=i)).isoformat(),
            "coupon_payments_bn": 0.0,
            "notes_bonds_bn": 0.0,
            "tips_bn": 0.0,
            "securities_count": 0,
            "coverage": "notes-bonds-tips-ex-soma",
            "mspd_record_date": mspd_date,
            "soma_as_of": soma_as_of,
            "frn_included": False,
        }
        for i in range((end - start).days + 1)
    }

    used = 0
    skipped_no_cusip = 0
    skipped_no_rate = 0

    for row in mspd_rows:
        cls = mspd_class(row).lower()

        is_note = "note" in cls and "floating" not in cls
        is_bond = "bond" in cls
        is_tips = "inflation" in cls or "tips" in cls
        is_frn = "floating" in cls or "frn" in cls

        if is_frn:
            # Exact FRN coupon requires the daily 13-week bill index over the
            # accrual period. Keep it out rather than inject a rough proxy.
            continue
        if not (is_note or is_bond or is_tips):
            continue

        cusip = mspd_cusip(row)
        if not cusip:
            skipped_no_cusip += 1
            continue

        maturity_s = date_only(
            first(row, "maturity_date")
            or first_semantic(row, "maturity", "date")
        )
        if not maturity_s:
            continue

        try:
            maturity = date.fromisoformat(maturity_s)
        except ValueError:
            continue

        coupon_rate = number(
            first(row, "interest_rate_pct", "interest_rate")
            or first_semantic(row, "interest", "rate")
        )
        if coupon_rate is None:
            skipped_no_rate += 1
            continue

        outstanding_mil = number(
            first(row, "outstanding_amt", "current_month_outstanding_amt")
            or first_semantic(row, "outstanding", "amt")
        )
        if outstanding_mil is None or outstanding_mil <= 0:
            continue

        total_outstanding_usd = outstanding_mil * 1_000_000.0

        soma = soma_by_cusip.get(cusip, {"par_usd": 0.0, "inflation_usd": 0.0})
        soma_usd = soma["par_usd"] + (soma["inflation_usd"] if is_tips else 0.0)

        public_outstanding_usd = max(total_outstanding_usd - soma_usd, 0.0)
        if public_outstanding_usd <= 0:
            continue

        coupon_bn = (
            public_outstanding_usd
            * (coupon_rate / 100.0)
            / 2.0
            / 1_000_000_000.0
        )
        if coupon_bn <= 0:
            continue

        payment_dates = coupon_payment_dates(maturity, start, end)
        for paid in payment_dates:
            key = paid.isoformat()
            if key not in schedule:
                continue
            schedule[key]["coupon_payments_bn"] += coupon_bn
            if is_tips:
                schedule[key]["tips_bn"] += coupon_bn
            else:
                schedule[key]["notes_bonds_bn"] += coupon_bn
            schedule[key]["securities_count"] += 1
            used += 1

    rows = []
    for d in sorted(schedule):
        item = schedule[d]
        for field in ("coupon_payments_bn", "notes_bonds_bn", "tips_bn"):
            item[field] = round(item[field], 6)
        rows.append(item)

    return {
        "rows": rows,
        "mspd_record_date": mspd_date,
        "soma_as_of": soma_as_of,
        "mspd_rows": len(mspd_rows),
        "soma_rows": len(soma_rows),
        "coupon_events": used,
        "skipped_no_cusip": skipped_no_cusip,
        "skipped_no_rate": skipped_no_rate,
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

        if old is None:
            dedup[key] = row
        elif old.get("status") == "actual":
            dedup[key] = merge_announcement_into_result(old, row)
        else:
            dedup[key] = merge_rows(old, row)

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
        fiscal_error = str(e)

    coupon = None
    coupon_error = None
    try:
        coupon = build_coupon_schedule()
    except Exception as e:
        coupon_error = str(e)

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

    coupon_response = None
    if coupon is not None:
        payload = {
            "source": "github-actions-treasury",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "auctions": [],
            "coupon_schedule": coupon["rows"],
        }
        status, coupon_response = post_json_retry(
            INGEST_URL, payload, INGEST_TOKEN
        )

    print(json.dumps({
        "treasurydirect_auctioned_rows":
            len(auctioned) if isinstance(auctioned, list) else 0,
        "treasurydirect_announced_rows":
            len(announced) if isinstance(announced, list) else 0,
        "fiscaldata_rows": fiscal_count,
        "fiscaldata_error": fiscal_error,
        "dedup_rows": len(all_rows),
        "maturity_metadata_rows": sum(
            1 for row in all_rows
            if row.get("maturity_source") == "treasurydirect-announcement"
            and row.get("est_pub_held_mat_by_type_amt") is not None
        ),
        "cash_metadata_rows": sum(
            1 for row in all_rows
            if row.get("cash_price_per_100") is not None
        ),
        "coupon_schedule_error": coupon_error,
        "coupon_schedule": None if coupon is None else {
            "mspd_record_date": coupon["mspd_record_date"],
            "soma_as_of": coupon["soma_as_of"],
            "mspd_rows": coupon["mspd_rows"],
            "soma_rows": coupon["soma_rows"],
            "coupon_events": coupon["coupon_events"],
            "days": len(coupon["rows"]),
            "skipped_no_cusip": coupon["skipped_no_cusip"],
            "skipped_no_rate": coupon["skipped_no_rate"],
        },
        "coupon_ingest_response": coupon_response,
        "batches": len(responses),
        "accepted": total_accepted,
        "skipped": total_skipped,
        "responses": responses,
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Daily research model. No networking, regression-based trading signals or fitting of event thresholds."""
from __future__ import annotations

import calendar
import hashlib
import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Iterable

MODEL_ID = "tga-public-face-residual-v1"
MODEL_VERSION = "3.3.0"
REFERENCE_END = "2026-07-22"


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip().replace(",", "").replace("$", "")
    if s.lower() in {"", "null", "none", "nan", "n/a", "."}:
        return None
    try:
        n = float(s)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        d = date.fromisoformat(value[:10])
        return d.isoformat()
    except ValueError:
        return None


def nth_weekday(y: int, m: int, w: int, n: int) -> date:
    d = date(y, m, 1)
    return d + timedelta(days=(w - d.weekday()) % 7 + (n - 1) * 7)


def easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e, f = b // 4, b % 4, (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * l) // 451
    q = h + l - 7 * n + 114
    return date(y, q // 31, q % 31 + 1)


def holidays(y: int, kind: str) -> set[date]:
    """Fed Banks do not close on Friday for Saturday holidays; exchanges generally do.
    Extraordinary exchange closures must be added explicitly; missing closes are never forward-filled.
    """
    if kind not in {"fed", "market"}:
        raise ValueError("calendar must be fed or market")
    out = {nth_weekday(y, 1, 0, 3), nth_weekday(y, 2, 0, 3),
           nth_weekday(y, 9, 0, 1), nth_weekday(y, 11, 3, 4)}
    last = date(y, 5, 31)
    out.add(last - timedelta(days=last.weekday()))
    fixed = [(1, 1), (7, 4), (12, 25)]
    if y >= 2022:
        fixed.append((6, 19))
    if kind == "fed":
        fixed.append((11, 11))
        out.add(nth_weekday(y, 10, 0, 2))
    else:
        out.add(easter(y) - timedelta(days=2))
    for m, day in fixed:
        d = date(y, m, day)
        if d.weekday() == 6:
            d += timedelta(days=1)
        elif d.weekday() == 5 and kind == "market" and (m, day) != (1, 1):
            d -= timedelta(days=1)
        out.add(d)
    # Exchange special closure; retained for reproducible historical calendars.
    if kind == "market" and y == 2025:
        out.add(date(2025, 1, 9))
    return out


def business_day(d: date, kind: str = "market", extra_closed: Iterable[str] = ()) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year, kind) and d.isoformat() not in set(extra_closed)


def sessions(start: str, end: str, kind: str = "market", extra_closed: Iterable[str] = ()) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    result = []
    while a <= b:
        if business_day(a, kind, extra_closed):
            result.append(a.isoformat())
        a += timedelta(days=1)
    return result


def previous_day(d: str, kind: str = "market", extra_closed: Iterable[str] = ()) -> str:
    a = date.fromisoformat(d) - timedelta(days=1)
    while not business_day(a, kind, extra_closed):
        a -= timedelta(days=1)
    return a.isoformat()


def asof_fed(d: str) -> str:
    a = date.fromisoformat(d)
    while not business_day(a, "fed"):
        a -= timedelta(days=1)
    return a.isoformat()


def regression(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Unweighted descriptive OLS. Tiny/constant samples have no r or fit."""
    xy = [(numeric(p.get("x")), numeric(p.get("y"))) for p in points]
    xy = [(x, y) for x, y in xy if x is not None and y is not None]
    n = len(xy)
    out = {"n": n, "r": None, "slope": None, "intercept": None}
    if n < 3:
        return out
    mx, my = sum(x for x, _ in xy) / n, sum(y for _, y in xy) / n
    sx = sum((x - mx) ** 2 for x, _ in xy)
    sy = sum((y - my) ** 2 for _, y in xy)
    cov = sum((x - mx) * (y - my) for x, y in xy)
    if sx <= 1e-16 or sy <= 1e-16:
        return out
    out.update(r=max(-1.0, min(1.0, cov / math.sqrt(sx * sy))), slope=cov / sx,
               intercept=my - cov / sx * mx)
    return out


def input_hash(row: dict[str, Any]) -> str:
    fields = {k: row.get(k) for k in ("date", "tga_bn", "prior_tga_bn", "net_issuance_bn",
               "sox_close", "tga_source_date", "net_status", "model_id")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, allow_nan=False).encode()).hexdigest()


def analyse(rows: list[dict[str, Any]], *, extra_closed: Iterable[str] = ()) -> dict[str, Any]:
    """U(k)=abs(sum signed residuals), NOT sum(abs(residuals)).
    Outcomes: t..t+4 closing-price trough; day t+5 may be BELOW that trough.
    Analysis is event-date descriptive, never an available-at trading backtest.
    """
    if not rows:
        return {"points": {"drawdown": [], "rebound": []}, "recent": [], "daily": [],
                "stats": {k: regression([]) for k in ("drawdown", "rebound")}, "latest_macro": None}
    bydate = {r["date"]: r for r in rows if iso_date(r.get("date"))}
    ds = sessions(min(bydate), max(bydate), extra_closed=extra_closed)
    residuals: dict[str, float | None] = {}
    derived = []
    for i, d in enumerate(ds):
        r = bydate.get(d, {})
        tga, prior, issuance = (numeric(r.get(k)) for k in ("tga_bn", "prior_tga_bn", "net_issuance_bn"))
        residuals[d] = None if None in (tga, prior, issuance) else tga - prior - issuance
        u = {}
        for k in (1, 3, 4, 5):
            vals = [residuals.get(x) for x in ds[max(0, i-k+1):i+1]]
            u[k] = abs(sum(vals)) if len(vals) == k and all(v is not None for v in vals) else None
        base = numeric(r.get("sox_close"))
        prices = [numeric(bydate.get(x, {}).get("sox_close")) for x in ds[i:i+6]]
        complete4 = len(prices) >= 5 and all(v is not None and v > 0 for v in prices[:5])
        complete5 = complete4 and len(prices) == 6 and prices[5] is not None and prices[5] > 0
        dd = (1 - min(prices[:5]) / base) * 100 if complete4 else None
        rb = (prices[5] / min(prices[:5]) - 1) * 100 if complete5 else None
        ready = 0
        for p in prices[1:]:
            if p is None or p <= 0:
                break
            ready += 1
        derived.append({"date": d, "u1_bn": u[1], "u4_bn": u[4], "u3_bn": u[3], "u5_bn": u[5],
            "signed_residual_bn": residuals[d], "delta_tga_bn": (tga-prior) if tga is not None and prior is not None else None,
            "net_issuance_bn": issuance, "drawdown_pct": dd, "rebound_pct": rb,
            "observed_sessions": ready, "macro_ready": u[1] is not None,
            "first_complete_seen_at": r.get("first_complete_seen_at"), "net_status": r.get("net_status"),
            "tga_source_date": r.get("tga_source_date"), "issues": r.get("issues", []),
            "status4": "complete" if u[1] is not None and dd is not None else "pending",
            "status5": "complete" if u[4] is not None and rb is not None else "pending"})
    pts = {"drawdown": [], "rebound": []}
    for x in derived:
        for name, k, y, status in (("drawdown", "u1_bn", "drawdown_pct", "status4"),
                                   ("rebound", "u4_bn", "rebound_pct", "status5")):
            if x[status] == "complete":
                pts[name].append({"date": x["date"], "x": x[k], "y": x[y],
                    "delta_tga_bn": x["delta_tga_bn"], "net_issuance_bn": x["net_issuance_bn"],
                    "first_complete_seen_at": x["first_complete_seen_at"]})
    latest = next((x for x in reversed(derived) if x["macro_ready"]), None)
    return {"points": pts, "stats": {k: regression(v) for k, v in pts.items()},
            "recent": list(reversed(derived[-12:])), "daily": derived, "latest_macro": latest}

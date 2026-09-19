"""Realized earnings-reaction move distributions.

The reaction session is derived from the SEC 8-K Item 2.02 acceptance timestamp,
not from a vendor's pre/post label: a filing accepted at or after 16:00 ET moves
the next session, anything earlier moves the session it was filed in.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
ET = ZoneInfo("America/New_York")
SEC_UA = "research kapil@example.com"
BARS = "https://data.alpaca.markets/v2/stocks/bars"

KEY = os.environ["ALPACA_API_KEY"]
SEC = os.environ["ALPACA_API_SECRET"]
ALPACA_HDRS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}


def get_json(url: str, headers: dict, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 - transient HTTP, retry
            if attempt == tries - 1:
                raise
            code = getattr(exc, "code", None)
            time.sleep(2.0 * (attempt + 1) if code in (429, 503) else 1.0)
    return {}


# ---------------------------------------------------------------- SEC EDGAR


def cik_map() -> dict[str, str]:
    p = HERE / "cik.json"
    raw = json.loads(p.read_text())
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in raw.values()}


def earnings_events(cik: str) -> list[tuple[str, str]]:
    """(filing_date, acceptance_datetime) for every 8-K carrying Item 2.02."""
    cache = HERE / "cache" / f"sub_{cik}.json"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists():
        doc = json.loads(cache.read_text())
    else:
        doc = get_json(
            f"https://data.sec.gov/submissions/CIK{cik}.json", {"User-Agent": SEC_UA}
        )
        cache.write_text(json.dumps(doc))
        time.sleep(0.15)  # SEC fair-access

    out: list[tuple[str, str]] = []
    blocks = [doc["filings"]["recent"]]
    for extra in doc["filings"].get("files", []):
        ec = HERE / "cache" / f"sub_{cik}_{extra['name']}"
        if ec.exists():
            blocks.append(json.loads(ec.read_text()))
        else:
            b = get_json(
                f"https://data.sec.gov/submissions/{extra['name']}",
                {"User-Agent": SEC_UA},
            )
            ec.write_text(json.dumps(b))
            time.sleep(0.15)
            blocks.append(b)

    for b in blocks:
        forms = b.get("form", [])
        items = b.get("items", [""] * len(forms))
        fdates = b.get("filingDate", [])
        accs = b.get("acceptanceDateTime", [""] * len(forms))
        for f, it, fd, acc in zip(forms, items, fdates, accs):
            if f == "8-K" and "2.02" in (it or ""):
                out.append((fd, acc))
    return sorted(set(out))


def reaction_is_next_session(acceptance: str) -> bool:
    """True when the filing landed at/after the 16:00 ET close."""
    if not acceptance:
        return True  # most issuers report after the close
    dt = datetime.fromisoformat(acceptance.replace("Z", "+00:00")).astimezone(ET)
    return (dt.hour, dt.minute) >= (16, 0)


# ---------------------------------------------------------------- price bars


def daily_bars(symbol: str, start: str) -> list[dict]:
    cache = HERE / "cache" / f"bars_{symbol}_{start}.json"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists():
        return json.loads(cache.read_text())
    rows: list[dict] = []
    token = None
    while True:
        q = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start,
            "feed": "sip",
            "adjustment": "all",
            "limit": "10000",
        }
        if token:
            q["page_token"] = token
        doc = get_json(f"{BARS}?{urlencode(q)}", ALPACA_HDRS)
        rows += doc.get("bars", {}).get(symbol, []) or []
        token = doc.get("next_page_token")
        if not token:
            break
    rows = [{"d": b["t"][:10], "c": b["c"], "o": b["o"], "v": b["v"]} for b in rows]
    cache.write_text(json.dumps(rows))
    return rows


def reaction_moves(symbol: str, cik: str, since: str = "2016-01-01") -> list[dict]:
    """Close-before -> close-after percentage move for each earnings event."""
    bars = daily_bars(symbol, since)
    if not bars:
        return []
    idx = {b["d"]: i for i, b in enumerate(bars)}
    sessions = [b["d"] for b in bars]
    out = []
    for fd, acc in earnings_events(cik):
        if fd < since:
            continue
        # first session on/after the filing date
        i = next((k for k, s in enumerate(sessions) if s >= fd), None)
        if i is None:
            continue
        nxt = reaction_is_next_session(acc)
        if nxt:
            # filing landed after the close of session i (or i is the filing day)
            base = i if sessions[i] == fd else i - 1
            react = base + 1
        else:
            base, react = i - 1, i
        if base < 0 or react >= len(bars):
            continue
        p0, p1 = bars[base]["c"], bars[react]["c"]
        if not p0:
            continue
        out.append(
            {
                "filed": fd,
                "reaction_date": bars[react]["d"],
                "after_hours": nxt,
                "ret": (p1 / p0) - 1.0,
                "px_before": p0,
            }
        )
    return sorted(out, key=lambda r: r["reaction_date"])


if __name__ == "__main__":
    import sys

    cm = cik_map()
    for sym in sys.argv[1:]:
        mv = reaction_moves(sym, cm[sym])
        absr = sorted(abs(m["ret"]) for m in mv)
        n = len(absr)
        print(f"\n{sym}: {n} earnings reactions since 2016")
        if not n:
            continue
        med = absr[n // 2]
        print(f"  median |move| {med:6.2%}   mean {sum(absr)/n:6.2%}   max {absr[-1]:6.2%}")
        for m in mv[-6:]:
            print(
                f"   {m['reaction_date']}  {m['ret']:+7.2%}  "
                f"{'AH' if m['after_hours'] else 'BMO'}"
            )

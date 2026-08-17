"""Matriks historical OHLC bar helper (dumrul/v1/tick/bar).

Canlı feed ile aynı MarketDataToken (C6) üzerinden gerçek-zamanlı kalitede tarihsel mum verisi
(dakikalık/saatlik/günlük). Tick-level history (tick/trade, tick/depth) ayrı lisans ister (EACCES);
bar (OHLC) erişilebilir.

Auth: `Authorization: jwt <MarketDataToken>` (Bearer DEĞİL) + Origin header.

Yanıt (bar başına): time(epoch ms), date, open, high, low, close, volume(TL notional),
quantity(adet), vwap. Eskiden yeniye sıralı.

    from matriks_wrapper.history import get_bars
    bars = get_bars("GARAN", "1day", count=300)
    bars = get_bars("GARAN", "1min", start="2026-06-01", end="2026-06-13")
"""
import gzip
import json
import time
from datetime import datetime, timedelta

import requests

from . import config
from .auth import mint_token

BAR_URL = "https://apiank.matriksdata.com/dumrul/v1/tick/bar.gz"
BAR_CSV_URL = "https://apiank.matriksdata.com/dumrul/v1/tick/bar.csv"

# REST'te doğrulanmış çalışan period'lar (bundle 10/15/30/120/240min de listeler ama REST 400 döner;
# chart bunları client-side 1min/5min'den türetir). Güvenli set:
VALID_PERIODS = ("1min", "5min", "1hour", "1day")

# Bar API tek istekte ~17.000 bar'dan fazlasını reddediyor (1min 60gün≈17k OK, 61gün→400).
# start/end aralığını period başına güvenli takvim-günü pencerelerine bölüp birleştiriyoruz.
_MAX_DAYS = {"1min": 45, "5min": 150, "1hour": 700, "1day": 3000}
_COUNT_CAP = 900  # count parametresi ~1000'de 400 döner


def _token(token=None):
    if token:
        return token
    tok, _lic = mint_token()
    return tok.strip()


def _params(symbol, period, count, timestamp, start, end):
    if period not in VALID_PERIODS:
        raise ValueError(f"Geçersiz period '{period}'. Desteklenen: {VALID_PERIODS}")
    p = {"symbol": symbol, "period": period}
    if start and end:
        p["start"] = start          # YYYY-MM-DD
        p["end"] = end
    else:
        p["count"] = count
        p["timestamp"] = timestamp if timestamp is not None else int(time.time() * 1000)  # epoch MS
    return p


def _fetch_window(symbol, period, params_extra, token, timeout):
    headers = {"Authorization": "jwt " + token, "Origin": config.ORIGIN, "Accept-Encoding": "gzip"}
    r = requests.get(BAR_URL, headers=headers,
                     params={"symbol": symbol, "period": period, **params_extra}, timeout=timeout)
    r.raise_for_status()
    body = r.content
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    raw = json.loads(body)
    if not isinstance(raw, list):
        raise RuntimeError(f"Beklenmeyen bar yanıtı: {str(raw)[:200]}")
    return raw


def get_bars(symbol, period="1day", count=300, timestamp=None,
             start=None, end=None, token=None, timeout=30):
    """OHLC bar listesi (eskiden yeniye). Zaman aralığı: count+timestamp(ms) VEYA start+end(YYYY-MM-DD).
    Geniş start/end aralığı period'a göre otomatik chunk'lanır (API ~17k bar/istek limiti)."""
    if period not in VALID_PERIODS:
        raise ValueError(f"Geçersiz period '{period}'. Desteklenen: {VALID_PERIODS}")
    tok = _token(token)
    raw = []
    if start and end:
        max_days = _MAX_DAYS.get(period, 45)
        d0 = datetime.strptime(str(start)[:10], "%Y-%m-%d")
        d1 = datetime.strptime(str(end)[:10], "%Y-%m-%d")
        cur = d0
        while cur <= d1:
            chunk_end = min(cur + timedelta(days=max_days - 1), d1)
            raw += _fetch_window(symbol, period,
                                 {"start": cur.strftime("%Y-%m-%d"), "end": chunk_end.strftime("%Y-%m-%d")},
                                 tok, timeout)
            cur = chunk_end + timedelta(days=1)
    else:
        c = min(int(count), _COUNT_CAP)
        ts = timestamp if timestamp is not None else int(time.time() * 1000)
        raw = _fetch_window(symbol, period, {"count": c, "timestamp": ts}, tok, timeout)

    # zaman'a göre tekilleştir + sırala (chunk sınırında olası tekrar bar)
    seen, out = set(), []
    for b in sorted(raw, key=lambda x: x.get("time") or 0):
        t = b.get("time")
        if t in seen:
            continue
        seen.add(t)
        out.append({
            "time": t, "date": b.get("date"),
            "open": b.get("open"), "high": b.get("high"), "low": b.get("low"),
            "close": b.get("close"), "volume": b.get("volume"),
            "quantity": b.get("totalQuantity"), "vwap": b.get("weightedAverage"),
        })
    return out


def get_bars_csv(symbol, period="1day", start=None, end=None, count=300, timestamp=None,
                 token=None, timeout=30):
    """Ham CSV (chart/Excel için). bar.csv endpoint'i."""
    headers = {"Authorization": "jwt " + _token(token), "Origin": config.ORIGIN}
    r = requests.get(BAR_CSV_URL, headers=headers,
                     params=_params(symbol, period, count, timestamp, start, end), timeout=timeout)
    r.raise_for_status()
    return r.text

"""Matriks symbol reference data (dumrul/v4/meta/symbols).

The full instrument universe in one call: equities, futures, options, indices, FX,
warrants/certificates, bonds — with underlying, strike, expiry, call/put and contract
size, so you can build a symbol master without scraping screens.

Auth: literal ``Authorization: jwt <MarketDataToken>`` (NOT Bearer) + Origin header —
same token the live feed uses, minted by :func:`matriks_wrapper.auth.mint_token`.

    from matriks_wrapper.symbols import fetch_symbols, normalize

    raw = fetch_symbols()                       # ~40k rows, vendor field names
    rows = normalize(raw)                       # readable keys
    opts = [r for r in rows if r["type"] == "option" and r["underlying"] == "GARAN"]

Vendor field glossary (as returned by the API):
    sC   symbol code                 sSC  subscription code (feed topic code)
    sT   instrument type (see TYPES) ds   description
    d    deleted flag                u    underlying symbol
    ma   maturity / expiry date (YYYY-MM-DD)
    oC   option class: C=call, P=put
    sP   strike price                m    multiplier / contract size
    ind  index memberships (equities: BIST100/BIST30/BIST50/BISTTUM...)
    eC   exchange segment
"""
import gzip
import json

import requests

from . import config

META_URL = "https://apiank.matriksdata.com/dumrul/v4/meta/symbols.gz"

#: Vendor ``sT`` code -> readable instrument type.
TYPES = {
    "S": "equity",
    "F": "future",
    "O": "option",
    "I": "index",
    "X": "fx",          # FX pairs + spot commodities (silver/platinum/palladium/WTI...)
    "E": "fx",          # Brent
    "C": "certificate",
    "B": "bond",
    "D": "fx_future",   # VIOP currency futures
    "R": "rights",
    "M": "fund",        # ETFs and exchange-listed funds
    "V": "warrant",
    "K": "participation",
}


def load_token(token=None):
    """A usable MarketDataToken.

    Prefers a fresh mint (C6 flow — no OTP, does not disturb an MQTT session); falls back
    to ``$MTX_HOME/.secrets/jwt.txt`` written by the running feed.
    """
    if token:
        return token.strip()
    try:
        from .auth import mint_token
        tok, _lic = mint_token()
        if tok:
            return tok.strip()
    except Exception:
        pass
    tok = config.load_token()
    if not tok:
        raise RuntimeError(
            "No MarketDataToken. Run `matriks-login` once, or pass token=... explicitly."
        )
    return tok


def fetch_symbols(token=None, deleted=False, all_symbols=False, timeout=60):
    """Raw symbol master as returned by the vendor (list of dicts, vendor field names).

    deleted     include de-listed instruments
    all_symbols include instruments outside your licence scope
    """
    headers = {
        "Authorization": "jwt " + load_token(token),
        "Origin": config.ORIGIN,
        "Accept": "*/*",
    }
    params = {"deleted": str(deleted).lower(), "allSymbols": str(all_symbols).lower()}
    r = requests.get(META_URL, headers=headers, params=params, timeout=timeout)
    r.raise_for_status()
    body = r.content
    if body[:2] == b"\x1f\x8b":                 # gzip magic
        body = gzip.decompress(body)
    return json.loads(body)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize(rows):
    """Vendor rows -> readable dicts.

    Keys: symbol, feed_code, type, raw_type, description, underlying, expiry, strike,
    call_put, contract_size, indices, segment, deleted.
    ``feed_code`` is what you put in a watchlist / MQTT topic.
    """
    out = []
    for s in rows or []:
        out.append({
            "symbol": s.get("sC"),
            "feed_code": s.get("sSC") or s.get("sC"),
            "type": TYPES.get(s.get("sT"), "other"),
            "raw_type": s.get("sT"),
            "description": s.get("ds"),
            "underlying": s.get("u") or None,
            "expiry": s.get("ma") or None,
            "strike": _num(s.get("sP")),
            "call_put": {"C": "call", "P": "put"}.get(s.get("oC")),
            "contract_size": _num(s.get("m")),
            "indices": s.get("ind") or [],
            "segment": s.get("eC"),
            "deleted": bool(s.get("d")),
        })
    return out


def main():
    """`python -m matriks_wrapper.symbols` — type breakdown, a sanity check that auth works."""
    from collections import Counter
    rows = fetch_symbols()
    n = Counter(TYPES.get(s.get("sT"), "other") for s in rows)
    print(f"{len(rows)} symbols")
    for k, v in n.most_common():
        print(f"  {k:14} {v}")


if __name__ == "__main__":
    main()

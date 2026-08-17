"""Auth manager: mint/refresh the Matriks MarketDataToken (JWT) via the Ziraat gateway.

Token is minted by POSTing `GetUrl.aspx MsgType=C6` with stored session material (captured once
at login). No captcha on C6 — only the initial login needs captcha + OTP. The JWT lives ~5 h; we
refresh well before expiry.

Session material lives in .secrets/session_store.json (gitignored), captured by the login step:
{ CustomerNo, Username, Password, SessionKey, ParentRef, HardwareID, SourceID, Version, ExchangeID }
"""
import base64
import json
import os
import time
import urllib.parse
import urllib.request

from . import config

GATEWAY = os.environ.get(
    "MTX_GATEWAY_URL",
    "https://mobil.ziraatyatirim.com.tr/0379_webtrader_px/GetUrl.aspx",
)
SESSION_FILE = os.path.join(config.ROOT, ".secrets", "session_store.json")


class SessionExpired(Exception):
    """The Ziraat session is no longer valid — a fresh remote login (captcha+OTP) is required.
    Distinct from transient network errors, which should be retried, not re-logged-in."""


def load_session():
    if not os.path.exists(SESSION_FILE):
        raise FileNotFoundError(
            "no .secrets/session_store.json — run the login capture once (track-4 bootstrap)")
    return json.load(open(SESSION_FILE, encoding="utf-8"))


def _clordid():
    # opaque unique id; server only needs uniqueness/echo
    return f"{int(time.time()*1000)}{os.getpid()%10000:04d}"


def jwt_claims(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def jwt_ttl(token):
    try:
        return jwt_claims(token).get("exp", 0) - int(time.time())
    except Exception:
        return 0


def mint_token(session=None, timeout=20):
    """POST C6 → return MarketDataToken (JWT). Raises on failure."""
    s = session or load_session()
    form = {
        "MsgType": "C6",
        "CustomerNo": s["CustomerNo"],
        "Username": s["Username"],
        "Password": s["Password"],
        "SessionKey": s["SessionKey"],
        "SourceID": s.get("SourceID", "70"),
        "Version": s.get("Version", "5419"),
        "ClientIP": "127.0.0.1",
        "P": "D",
        "Language": "tr",
        "sso": "false",
        "AccountID": "0",
        "ClOrdID": _clordid(),
        "ExchangeID": s.get("ExchangeID", "4"),
        "ParentRef": s["ParentRef"],
        "HardwareID": s["HardwareID"],
        "ETX": "1",
    }
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(GATEWAY, data=data, method="POST", headers={
        "Origin": config.ORIGIN,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "matriks-wrapper",
    })
    # Network/HTTP errors propagate as-is (transient -> caller retries, no re-login).
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    res = resp.get("Result", {})
    if not res.get("State"):
        # Gateway answered but rejected -> the session is dead -> needs re-login.
        raise SessionExpired(f"C6 rejected: code={res.get('Code')} {res.get('Description')!r}")
    tok = resp.get("MarketDataToken")
    if not tok:
        raise SessionExpired("C6 ok but no MarketDataToken (session likely invalid)")
    return tok, resp.get("LicenceList", [])


class AuthManager:
    """Holds the current JWT; refreshes before expiry. `current_token()` is the provider passed to
    the broker pool so reconnects always use a fresh token."""
    def __init__(self, refresh_margin=600):
        self.refresh_margin = refresh_margin   # refresh when <10 min left
        self._token = None
        self._exp = 0
        self.licences = []

    def current_token(self):
        if self._token is None or jwt_ttl(self._token) < self.refresh_margin:
            self.refresh()
        return self._token

    def refresh(self):
        tok, lic = mint_token()
        self._token = tok
        self._exp = jwt_claims(tok).get("exp", 0)
        self.licences = lic
        return tok

    def ttl(self):
        return jwt_ttl(self._token) if self._token else 0


if __name__ == "__main__":
    tok, lic = mint_token()
    print("minted JWT len", len(tok), "ttl", jwt_ttl(tok), "s")
    print("licences:", [l.get("LicenseCode") for l in lic])

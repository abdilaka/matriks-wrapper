# 03 – Authentication & JWT

> ⚠️ This file documents **structure only**. Never commit real session credentials or tokens.
> All live values below are redacted as `<…>`.

## Two separate systems

1. **Ziraat brokerage gateway** — `https://mobil.ziraatyatirim.com.tr/0379_webtrader_px/GetUrl.aspx`
   A multiplexed POST API keyed by `MsgType`. Handles login, account reports, orders **and**
   issues the market-data token. This is where auth/captcha/OTP live.
2. **Matriks market-data feed** — the MQTT brokers (`*.radix.matriksdata.com`). Authenticated by
   the JWT minted in (1).

## The market-data token call (`MsgType=C6`)

Once a session exists, the app POSTs (form-urlencoded):

```
POST /0379_webtrader_px/GetUrl.aspx
Origin: https://ziraatyatirim.matrikswebtrader.com
Content-Type: application/x-www-form-urlencoded

MsgType=C6
CustomerNo=<customerNo>        # e.g. the müşteri numarası
Username=<sessionUser>         # 6-digit session-scoped id (NOT the login password)
Password=<sessionPass>         # 6-digit session-scoped secret
SessionKey=<_uuid>             # issued by login
SourceID=70  Version=5419  ExchangeID=4  P=D  Language=tr
ClOrdID=<uniquePerCall>  ParentRef=<base64>  HardwareID=<fingerprint>  ETX=1
```

Response:
```json
{
  "Result": { "State": true, "Code": 0, "MsgType": "C6", ... },
  "MarketDataToken": "eyJhbGciOiJSUzI1Ni...",   ← the MQTT password (JWT)
  "LicenceList": [
    { "LicenseCode": "KD1",    "Name": "Karma Düzey 1",            "Status": "A" },
    { "LicenseCode": "IMKBEX", "Name": "Borsa İstanbul Endeksleri","Status": "A" }
  ]
}
```
`LicenceList` tells you the entitlements (which feeds/levels are realtime vs delayed).

## The JWT (`MarketDataToken`)

- **alg** RS256, **iss** `ZRTYAT`.
- Claims: `iss, exp, iat, nbf, id, sub, cli`. `sub` = `ZRY-<customerNo>`, `cli` = `"W"` (web).
- **Lifetime = 18000 s (5 hours)** (`exp − iat`), `nbf` ≈ iat − 30 s.
- Used verbatim as the **MQTT CONNECT password**; username is the constant `JTW`.

## Login chain (initial) — ✅ captured

Confirmed sequence (all `POST GetUrl.aspx` unless noted), keyed by `MsgType`:

| Step | MsgType | Key fields | Purpose |
|------|---------|-----------|---------|
| 0 | — | `GET CaptchaProxy.aspx/<SourceID>` | PNG captcha image |
| 1 | `A` | `CustomerNo, Username, Action, HardwareID` | submit müşteri no + captcha (`Action`) → `SessionKey`, **triggers OTP SMS** |
| 2 | `A` | `Username, Password, SessionKey, Otp` | submit parola + OTP SMS → `"Kimlik doğrulama … tamamlandı"` |
| 3 | — | `GET disco-v2.json` | broker/REST discovery |
| 4+ | `C3` | `CustomerNo, Username, Password, SessionKey, Code, ParentRef, HardwareID` | account/market login (returns account `Item`s, **not** the token) |
| — | `C6` | same material as C3 | **mint MarketDataToken (JWT)** + `LicenceList` |
| — | `R` | `…, SubType=r41, StartDate, EndDate` | account reports (cash statement) |

`Username`/`Password` from step 2 onward are **6-digit session-scoped** credentials (issued by the
login), not the user's parola. `Action` in step 1 carries the captcha solution. Response headers
expose `X-Matriks-Captcha-Id` / `X-Matriks-Captcha`.

The fields needed to re-mint tokens are captured once and stored in
`.secrets/session_store.json`: `{CustomerNo, Username, Password, SessionKey, ParentRef, HardwareID,
SourceID, Version, ExchangeID}`.

## Refresh strategy — ✅ working

- A human logs in **once** (captcha + OTP SMS); we capture session material → `session_store.json`.
- **Token refresh = `POST GetUrl.aspx MsgType=C6`** with that material → fresh 5 h JWT. **No
  captcha on C6.** Implemented in `src/auth.py` (`mint_token` / `AuthManager`) and **validated
  from pure Python** (returns JWT ttl 18000 s + `LicenceList` `[KD1, IMKBEX]`). C6 is plain HTTPS
  and does **not** disturb any live MQTT session.
- Open: the **session's own TTL** (how long C6 keeps working before re-login is required) — learn
  empirically. When C6 starts failing, fire the **login trigger / OTP webhook** (track 4 remainder)
  so the human re-auths.

## ⚠️ Single active session per account
Connecting the MQTT feed from a second place **kicks the first**: running the Python listener while
the browser is logged in logged the browser out (and vice-versa). So the headless service and any
manual browser/terminal session **cannot run concurrently** on the same account. The HTTPS token
mint (C6) is fine to call alongside a browser; only the **MQTT connection** is exclusive.

## Security notes
- Treat `SessionKey`, session `Username`/`Password`, `HardwareID`, `ParentRef` and the JWT as
  secrets → env / secret store, never in git.
- `HardwareID` is a device fingerprint; reusing the captured one keeps the session bound to our client.

# 09 – Live-listener service plan (tracks 2–4)

Target: a headless Python service that keeps a live, authenticated Matriks feed for a ~200-symbol
watchlist (indices, equities, futures, options) and persists ticks for downstream analytics
(your own analytics).

## Component overview

```
            ┌──────────────────────────────────────────────────────────┐
            │                    matriks-wrapper service                │
            │                                                          │
 .env ─────►│  Config        watchlist.yaml ─► SymbolUniverse          │
 (creds)    │    │                                  │                  │
            │    ▼                                  ▼                  │
            │  AuthManager ──JWT──►  BrokerPool ──►  Subscriptions     │
            │    │  ▲                    │ (1 MQTT/WS per broker)      │
            │    │  │ OTP                ▼                             │
            │    │  └──── LoginTrigger   Decoder (matriks.proto)       │
            │    │        (webhook)         │                          │
            │    ▼                          ▼                          │
            │  SessionStore           TickStore ──► local files        │
            │  (SessionKey, HW id)    (later: Redis streams)           │
            └──────────────────────────────────────────────────────────┘
                                          │
                         consumers: your analytics
```

## Modules (`src/`)

| Module | Responsibility |
|--------|----------------|
| `config.py` | Load `.env` (creds) + `watchlist.yaml`. |
| `auth.py` (`AuthManager`) | Hold session material; mint/refresh JWT via `GetUrl.aspx MsgType=C6`; expose `current_token()`; schedule refresh < 5 h; trigger `LoginTrigger` when session dead. |
| `login_trigger.py` | External login flow: webhook endpoint that, on demand, drives a login (captcha + OTP SMS entry by the human) and writes fresh session material to `SessionStore`. |
| `discovery.py` | Fetch `disco-v2.json`, resolve broker URL per topic prefix + QoS. |
| `broker.py` (`BrokerPool`) | One MQTT-3.1-over-WS connection per broker; `Origin` header; `uid=JTW`+JWT; auto-reconnect; re-push token on refresh. |
| `decode.py` | Map topic root → protobuf type; decode PUBLISH payload; merge into per-symbol snapshot (proto3 partial-update aware). |
| `store.py` (`TickStore`) | Persist ticks. v1: append JSONL/parquet per symbol locally. v2: Redis streams / hashes. |
| `service.py` | Wire it all; subscribe the watchlist; health/metrics. |

## Auth & refresh flow (track 4)

1. **Bootstrap (human, once):** `.env` has CustomerNo + login password. A `LoginTrigger` run opens
   login, human solves **captcha** + enters **OTP SMS** → capture `SessionKey`, session
   `Username`/`Password`, `HardwareID`, `ParentRef`. Persist to `SessionStore` (encrypted/secret).
2. **Token mint:** POST `GetUrl.aspx MsgType=C6` with session material → `MarketDataToken` (JWT, 5 h)
   + `LicenceList`.
3. **Refresh loop:** every ~4 h (before exp), re-mint via C6 (no captcha). Push new token to all
   broker connections (`setpassword` equivalent / reconnect).
4. **Session death:** if C6 starts failing (session expired), fire the `LoginTrigger` webhook so the
   human re-auths (captcha+OTP). Until then, serve last-known data and alert.

> ⏳ **Open items to capture next:** exact initial-login `MsgType`(s), captcha submit fields
> (`X-Matriks-Captcha-Id/-Captcha`), OTP/2FA SMS step fields, and the **session TTL** (how long C6
> keeps working before re-login). Capture by logging out → in while instrumenting `GetUrl.aspx`.

### Webhook for OTP (later)
A small endpoint on our domain: `POST /matriks/login/start` kicks off login and returns a state;
`POST /matriks/login/otp {code}` submits the SMS code the user receives. Keeps the secret-handling
server-side; the human only forwards the OTP.

## Live listener (track 3 — MQTT)

- Transport per [02](02-transport-mqtt.md): `websockets` + MQTT 3.1 (`paho`/`gmqtt`), `Origin` set,
  subprotocol `mqttv3.1`.
- Resolve brokers, group watchlist topics by broker, subscribe. Watch for license sub-limits /
  server `auto_unsubscribe`.
- Decode with compiled `matriks.proto`; keep a per-symbol live snapshot (apply partial updates).
- Persist every update to `TickStore`.

## Watchlist (~200)
`watchlist.yaml`: indices (XU100, XU030, XU050, XBANK…), equities (BIST-100 + extras), futures
(`X30YVADE`, `F<equity>`…), options (VIOP series). Resolve/validate codes against REST
`metaData.symbols`. Topic = `mx/symbol/<eq>`, `mx/symbol/<idx>@lvl2`, `mx/derivative/<fut|opt>`.

## Analytics consumers (user-side, downstream)
- **Option implied-vol:** option `last`/`theoricalPrice` + underlying + `initialMargin` + risk-free.
Read from `TickStore` (later Redis) so compute is decoupled from ingestion.

## Build order
1. `discovery.py` + `decode.py` + a throwaway script that connects with a **manually-pasted JWT**
   and prints decoded ticks for 5 symbols → proves the MQTT+protobuf path end-to-end.
2. `broker.py` + `store.py` + watchlist subscribe → persist.
3. `auth.py` C6 mint/refresh with captured session material.
4. `login_trigger.py` + OTP webhook → close the loop for unattended refresh.

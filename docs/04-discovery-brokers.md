# 04 – Discovery & brokers

## Discovery document

```
GET https://disco.matriksdata.com/disco-v2.json?issuer=ZRTYAT
Origin: https://ziraatyatirim.matrikswebtrader.com
```
Public (no auth), ~126 KB JSON. Two top-level blocks:
- `rest` → 277 REST endpoints (see [07](07-rest-api-catalog.md)). Full copy in
  `reference/disco-v2.full.json`; flat list in `reference/rest-catalog.tsv`.
- `mqtt` → 74 topic prefixes, each mapped to broker URLs per QoS tier and transport.

`issuer=ZRTYAT` = Ziraat Yatırım branding. Other brokers have other issuers; the structure is the same.

## MQTT broker map shape

```json
"mx/symbol": {
  "qos": {
    "rt":  "wss://rtank.radix.matriksdata.com:443/market",   // realtime
    "dl":  "",                                                // delayed (empty → use wss/dl)
    "wss": { "rt": "wss://rtank.radix.matriksdata.com/market",
             "dl": "wss://dlank.radix.matriksdata.com:443/market" },
    "ws":  { "rt": "ws://...",  "dl": "ws://..." },
    "tcp": { "rt": "tcp://...:34452", "dl": "tcp://...:34552" }
  }
}
```
- **`rt`** = realtime host (`rtank` / `rtstream`), **`dl`** = delayed host (`dlank`).
- We use the **`wss`** transport. Realtime needs the matching license; otherwise subscribe via the
  delayed broker (and/or the `@vq` topic suffix, see [05](05-topics.md)).

## Brokers actually used (this account)

| Broker URL | Carries |
|------------|---------|
| `wss://rtank.radix.matriksdata.com:443/market` | `mx/symbol/*`, `mx/derivative/*`, `mx/timestamp` |
| `wss://dlank.radix.matriksdata.com:443/market` | delayed `…@vq` variants |
| `wss://rtank.radix.matriksdata.com/news`       | `mx/news@*` |
| `wss://rtank.radix.matriksdata.com/stats`      | `mx/stats/*` |
| `wss://rt.radix.matriksdata.com/arf`           | `mx/arf` (alarms/rules) |
| `wss://rtstream.radix.matriksdata.com/foreignmarket` | `mx/iex/*`, `mx/nbbo/*` (foreign) |
| `wss://rtstream.radix.matriksdata.com:443/session`   | `mx/session` |

Full prefix→broker table: `reference/mqtt-topics.tsv`.

## Client guidance
Resolve broker URLs from the discovery doc at startup (don't hard-code — hosts can change). Group
your watchlist's topics by their broker and open one MQTT/WS connection per distinct broker.

# 05 – Topics

MQTT topic = `<prefix>/<SYMBOL>[@variant]`. Subscribe per symbol; the broker is chosen by the
prefix via the discovery map ([04](04-discovery-brokers.md)).

## Core prefixes

| Topic | Example | Payload type | Notes |
|-------|---------|--------------|-------|
| `mx/symbol/<CODE>` | `mx/symbol/AKBNK` | `SymbolMessage` | Equity / spot quote + fundamentals |
| `mx/symbol/<CODE>@lvl2` | `mx/symbol/XU100@lvl2` | `SymbolMessage` | Used for **indices** & **FX**; adds `eqPrice`/`eqQuantity` (opening-auction match) |
| `mx/symbol/<CODE>@vq` | `mx/symbol/AKBNK@vq` | `SymbolMessage` | **Delayed** variant → routed to `dlank` broker |
| `mx/derivative/<CODE>` | `mx/derivative/FGARAN` | `DerivativeMessage` | **VIOP** futures/options; adds `settlement`, `theoricalPrice`, `initialMargin` |
| `mx/derivative/<CODE>@vq` | … | `DerivativeMessage` | Delayed |
| `mx/depth/<CODE>` | `mx/depth/AKBNK` | `DepthTableMessage` | Order-book ladder |
| `mx/depthstats/<CODE>` | | `DepthStatsMessage` | Aggregated depth stats |
| `mx/trade/<CODE>` | | `TradeMessage` | Time & sales (individual prints) |
| `mx/timestamp` | (no symbol) | `TimeMessage` | Server clock (µs), ~1/s heartbeat |
| `mx/session` | | `SessionMessage` | Session open/close windows |
| `mx/stats/high`,`/low`,`/volume`(+`/weekly|monthly|yearly`) | | ranked list | Gainers/losers/volume leaders; payload `{symbols:[{key,value,last,priceChange}]}` |
| `mx/news@matriks|@comment|@kap|@int|@aa|@ai` | | `NewsMessage` | News & **KAP disclosures** (on `/news` broker) |
| `mx/pgc/<CODE>` | | `PgcMessage` | Price-grade/circuit |
| `mx/fundratio/<CODE>` | | `FundratioMessage` | Fund ratios |
| `mx/ratingscore/<CODE>` | | `RatingScoreMessage` | Rating score |
| `mx/event/prime/{symbol,agent,derivative,index}` | | `EventMessage` | Prime events |
| `mx/iex/symbol`, `mx/nbbo/symbol` | | (foreign) | US/foreign market (rtstream/foreignmarket) |

Full prefix→broker list: `reference/mqtt-topics.tsv`.

## Variant suffixes

- **`@lvl2`** — richer quote; the app uses it for indices (XU100, XU030, …) and FX
  (USDTRY, EURTRY, EURUSD, XAUUSD, GLDGR).
- **`@vq`** — "volume quote" / **delayed** stream; routed to the `dl` (dlank) broker. If you lack
  realtime entitlement for a symbol, this is the fallback.
- No suffix — realtime full quote on the `rt` broker.

## Symbol codes

- Equities: BIST tickers (`AKBNK`, `THYAO`, …).
- Indices: `XU100`, `XU030`, `XU050`, `XBANK`, …
- FX / metals: `USDTRY`, `EURTRY`, `EURUSD`, `XAUUSD`, `GLDGR` (gram gold).
- ETFs (BYF, *borsa yatırım fonu*): `ZPX30F`, `APX30F`, `OPK30F`, `Z30KPF`, `GLDTRF`, …
  Most ETF tickers end in `F`, but the reverse does not hold: `GUBRF` is an equity, `USDCHF` and
  `EURCHF` are FX, `XAKTIF` is an index. Use the instrument type from the symbol master, not the
  suffix.
- Futures: three shapes.
  `F_<underlying><MMYY>` is the canonical contract (`F_GARAN0826`, `F_XU0300826`, `F_EURTRY1226`).
  `F<equity>` (`FGARAN`, `FECILC`) and `<base>YVADE` (`X30YVADE`, `USDYVADE`) are front-month
  *aliases*: in the symbol master their `sSC` resolves to the canonical code, so `FGARAN` subscribes
  as `F_GARAN0826`. Subscribe with `sSC`, not the display code. Calendar spreads appear as
  `F_<underlying>M2-M1`.
- Options: `O_<underlying>E<MMYY><C|P><strike>`, e.g. `O_XU030E0826P15500.00`,
  `O_GARANE0826C140.00`. `E` marks European exercise; `C`/`P` is call/put.

Resolve the full tradable universe + metadata via REST `metaData.symbols`
(`/dumrul/v4/meta/symbols`) and `marketSymbolList` ([07](07-rest-api-catalog.md)). The `sT` field
there is authoritative for instrument type: `S` equity, `F` future, `O` option, `I` index, `X`/`E`
FX and commodities, `M` fund/ETF, `V` warrant, `C` certificate, `B` bond, `R` rights.

## Snapshot echo prefix (`mx/user/<sub>/…`)
On subscribe, the broker first replies with a **personalized snapshot** PUBLISH whose topic is
prefixed: `mx/user/<sub>/<realTopic>` (e.g. `mx/user/ZRY-190656/mx/symbol/AKBNK`, where `<sub>` is
the JWT `sub` claim). Strip the `mx/user/<sub>/` prefix to recover the real topic before choosing
the protobuf type. Subsequent live updates arrive on the plain topic. Confirmed working in
`src/poc_listen.py`.

## Partial updates
Live (non-snapshot) PUBLISHes are **sparse** — only changed proto fields are present (proto3
defaults omitted). Maintain a per-symbol snapshot dict and merge each update; never treat a missing
field as zero. Observed: a tick may carry just `{symbolCode, last}` or even `{symbolCode}`.

## Subscription model gotcha
On the web app each symbol is subscribed individually (≈1200 subscribes at startup for the default
layout). For a 200-symbol watchlist that's fine. Note the **per-connection live-subscription
limits** depend on license — verify against `LicenceList` and watch for `auto_unsubscribed`-style
server pushback (seen on the sibling feed).

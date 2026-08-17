# 06 – Protobuf schema

Schema file: **`proto/matriks.proto`** (`syntax=proto3`, package `mxroot.messages`, 19 messages).
Regenerate it from a fresh worker bundle with `reference/extract_proto.py`.

Compile for Python (output goes into the package so `from matriks_wrapper.proto import matriks_pb2`
works; the compiled file is committed, so you only need this when the schema changes):
```bash
pip install -e .[dev]   # brings grpcio-tools
python -m grpc_tools.protoc -I proto --python_out=src/matriks_wrapper/proto proto/matriks.proto
```

## Topic root → message type

| Topic prefix | Protobuf message |
|--------------|------------------|
| `mx/symbol/*` (incl. `@lvl2`, `@vq`) | `SymbolMessage` |
| `mx/derivative/*` | `DerivativeMessage` |
| `mx/depth/*` | `DepthTableMessage` |
| `mx/depthstats/*` | `DepthStatsMessage` |
| `mx/trade/*` | `TradeMessage` |
| `mx/timestamp` | `TimeMessage` |
| `mx/session` | `SessionMessage` |
| `mx/news@*` | `NewsMessage` |
| `mx/stats/*` | `RankedSymbolsMessage` / `RankerMessage` |
| `mx/pgc/*` | `PgcMessage` |
| `mx/fundratio/*` | `FundratioMessage` |
| `mx/ratingscore/*` | `RatingScoreMessage` |
| `mx/event/*` | `EventMessage` |

> The PUBLISH wire format is **bare protobuf** (the message body only) — the topic tells you which
> type to apply. Some sparse messages omit unchanged fields (proto3 defaults), so treat absent
> fields as "unchanged", not "zero", when maintaining a per-symbol state snapshot.

## SymbolMessage — key fields (full list in matriks.proto, 85 fields)

| # | Field | Type | Meaning |
|---|-------|------|---------|
| 1 | symbolId | sint32 | Numeric id |
| 2 | symbolCode | string | Ticker |
| 4 | updateDate | string | Last update ts (`YYYY-MM-DDTHH:mm:ss.SSS+03`) |
| 5/6 | bid / ask | double | Best bid / ask |
| 7/8 | low / high | double | Session low / high |
| 9 | last | double | Last price |
| 10 | dayClose | double | **Previous close** (reference) |
| 12/13 | dailyLow / dailyHigh | double | Day low / high |
| 14/15 | quantity / volume | double | Traded lots / TL volume |
| 16/17 | difference / differencePercent | double | Δ vs prev close (abs / %) |
| 21–24 | monthHigh/Low, yearHigh/Low | double | Range stats |
| 26/27 | limitUp / limitDown | double | Daily price limits (tavan/taban) |
| 28 | netProceeds | double | Net foreign/agent flow |
| 33/34 | equity / capital | double | Equity / paid capital |
| 35/36 | circulationShare / …Per | double | Free float (shares / %) |
| 41/42 | priceStep / basePrice | double | Tick size / base |
| 47 | open | double | Open |
| 48 | dailyQuantity | double | Day lots |
| 39/40 | sessionIsOpen / openForTrade | bool | Session / tradability flags |
| 50+ | weekLow/High/Close, monthClose, yearClose, beta100, dividendYield, netDebt, shiftedEbitda, eqPrice, eqQuantity … | | Extended stats (see proto) |

## DerivativeMessage — extra fields vs Symbol (VIOP)

Same quote core (bid/ask/last/dayClose/…) **plus**:

| Field | Meaning |
|-------|---------|
| settlement | Settlement price (uzlaşma) |
| preSettlement | Previous settlement |
| theoricalPrice | Theoretical price |
| initialMargin | Initial margin requirement |
| marketMaker / marketMakerBid / marketMakerAsk | Market-maker quotes |
| openForTrade | Tradability |

→ Exactly the inputs a pricing/analytics layer needs (futures `last/settlement`, underlying `last`)
and **option pricing / implied-vol** (option `last` + `theoricalPrice` + underlying + `initialMargin`).

## Other types (see proto for fields)
`TimeMessage{int64 timestamp}`, `SessionMessage{date, firstSession*, secondSession*, exchangeId,
marketId}`, `NewsMessage{id, timestamp, headline, content, source[], category[], symbol[],
attachment[], relatedNews[], aiAnalysis}`, `DepthTableMessage`, `TradeMessage`, `RatingScoreMessage`,
`FundratioMessage`, `PgcMessage`, `EventMessage`, `MetaMessage`, `ComputedValuesMessage`.

> Nested sub-types referenced by `RankerMessage`/`RankedSymbolsMessage` (`Symbol`, `Line`) live in a
> separate `w.messages.*` namespace in the worker and are not in `matriks.proto` yet — extract if
> you need `mx/stats` decoding beyond the simple `{symbols:[{key,value,last,priceChange}]}` shape.

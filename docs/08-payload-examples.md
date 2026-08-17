# 08 – Payload examples

Real decoded samples captured live (2026-06-12/14 session). Full set:
`reference/payload-examples.json`. Each message delivered by the worker has the envelope
`{ "model":"mqtt", "url":"<broker>", "topic":"<topic>", "payload":{…} }`; below shows `payload`.

## mx/timestamp → TimeMessage
```json
{ "timestamp": 1781394569000000 }
```
Microsecond epoch; ~1 Hz heartbeat on the market broker.

## mx/symbol/ARCLK → SymbolMessage (equity)
```json
{ "symbolId":44, "symbolCode":"ARCLK", "updateDate":"2026-06-12T18:09:41.000+03",
  "bid":102.9, "ask":103, "low":101.3, "high":104.7, "last":102.9, "dayClose":101.5,
  "dailyLow":101.3, "dailyHigh":104.7, "monthHigh":114.9, "monthLow":100.4,
  "yearHigh":147.5, "yearLow":99.65, "limitUp":113.1, "limitDown":92.65,
  "netProceeds":-1816779010, "equity":73314458000, "capital":675728205,
  "circulationShare":118720726.12, "circulationSharePer":17.56,
  "priceStep":0.1, "basePrice":102.9, "tradeDate":"12/06/26", "open":..., ... }
```

## mx/symbol/XU100@lvl2 → SymbolMessage (index)
```json
{ "symbolId":642, "symbolCode":"XU100", "updateDate":"2026-06-12T18:10:11.000+03",
  "low":13801.34, "high":14125.84, "last":13938.48, "dayClose":13743.5,
  "quantity":9534079304, "volume":229285837544.45,
  "monthHigh":14762.5, "yearHigh":15204.92, "yearLow":9065.17, ... }
```
FX example (`USDTRY@lvl2`): `{ last:46.2685, bid:46.2635, ask:46.2735, dayClose:46.186, open:46.1903 }`.
Gram gold (`GLDGR@lvl2`): `{ last:6273.503, dayClose:6252.85 }`.

## mx/derivative/X30YVADE → DerivativeMessage (VIOP future)
```json
{ "symbolId":1614, "symbolCode":"X30YVADE", "updateDate":"2026-06-12T22:59:58.000+03",
  "bid":16090, "ask":16093, "low":15783, "high":16337, "last":16090, "dayClose":15724,
  "limitUp":16509, "limitDown":15549,
  "settlement":16029, "theoricalPrice":16213.067, "preSettlement":...,
  "priceStep":1, "openForTrade":true, "open":15995, ... }
```
→ futures carry `last`, `settlement` and `theoricalPrice`; the index topic carries spot `last`.

## mx/stats/high → ranked list
```json
{ "symbols": [ { "key":"ARMGD", "value":10.0, "last":146.3, "priceChange":10.0 },
               { "key":"BRKO",  "value":10.0, "last":13.75, "priceChange":10.0 }, … ] }
```
Top gainers (limit-up at +10%). `mx/stats/low`, `/volume`, and the `weekly|monthly|yearly` variants
share this shape.

## Capture method (reproducible)
Instrument `MessagePort.prototype` in the page to tap the worker→app bridge; the worker emits the
already-decoded objects above. See `reference/extract_proto.py` for schema, and the session notes
for the DevTools `initScript` tap.

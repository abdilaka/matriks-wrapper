# 07 – REST API catalog

277 endpoints from the discovery `rest` block. Full machine-readable list:
`reference/rest-catalog.tsv` (name · url · method · params · gz). Bases:
- `https://apiank.matriksdata.com/dumrul/…` — main BIST data
- `https://apicloudank.matriksdata.com/dumrul/…` — foreign markets
- `https://api.matriksdata.com/dumrul/…` — user rules (ARF)

Common conventions: `GET`, optional `ngsw-bypass=true`, many support gzip (`gzAvailable`) via a
`.gz` path. Auth: most market-data reads work with the same JWT as a bearer / or are open; verify
per endpoint. Always send `Origin: https://ziraatyatirim.matrikswebtrader.com`.

## High-value endpoints by purpose

### Snapshots (bulk current state — good for cold start before MQTT)
| Name | URL | Params |
|------|-----|--------|
| snapshotMarketReal | `/dumrul/v1/snapshot-market/real` | symbols |
| snapshotMarketDelayed | `/dumrul/v1/snapshot-market/delayed` | symbols |
| marketSymbolList | `/dumrul/v2/market-symbol-list` | inline |

### OHLC / bars (historic & intraday)
| Name | URL | Params |
|------|-----|--------|
| bar | `/dumrul/v1/tick/bar` | symbol, period, start, end, count, divide, useFraction, …Timestamp |
| barCsv | `/dumrul/v1/tick/bar.csv` | symbol, period, start, end |
| lntrade | `/dumrul/v1/lntrade` | symbol, count (last N trades) |
| derivedBar | `/dumrul/v1/derived-bar` | (computed series) |

`period` examples seen: `1day`, intraday minute periods. e.g.
`/dumrul/v1/tick/bar.gz?symbol=XU100&period=1day&count=300`.

### Tick history (microstructure)
`historicTick.*` → `/dumrul/v1/tick/{trade,trade_bs,trade_ex,depth,bestbidoffer,settlement,
totalsize,openinterest}` — params `symbol,start,end`. Useful for backtesting.

### News & KAP
| Name | URL | Params |
|------|-----|--------|
| news.search | `/dumrul/v2/news/search` | query, language, count, withComment |
| news.lastNNews | `/dumrul/v2/news/lastN` | count, fields |
| news.id | `/dumrul/v2/news` | id, fields |
| news.page | `/dumrul/v2/news/search/page` | content, page, pageSize, qid |

(KAP disclosures arrive on the `mx/news@kap` MQTT topic and via these search endpoints.)

### Corporate actions / fundamentals
| Name | URL | Params |
|------|-----|--------|
| dividends | `/dumrul/v1/dividends` | symbol, start, end |
| capitalIncrease | `/dumrul/v1/capital-increase` | symbol… |
| shareBuyback | `/dumrul/v1/share-buyback` | |
| publicOffering | `/dumrul/v1/public-offering` | |
| fundamentals.BS / .CF / .INC (+`.csv`) | `/dumrul/v1/fundamentals/{BS,CF,INC}` | symbols, periods, currency, lang, unadjusted |
| fundamentalIndicators | `/dumrul/v1/fundamentals-indicators` | symbols |
| ratioAnalysis / historicRatio | `/dumrul/v1/…ratio…` | |
| profitTable | `/dumrul/v1/profit-table` | |
| circulation | `/dumrul/v1/circulation` | (free-float) |

### Flows & broker activity
trade-distribution (`/dumrul/v1/trade-distribution/{brokers,equities,equity}`),
broker-trading-volume (`/stock|future|option`), agent-assets (`/dumrul/v1/agent-assets`),
akd / akdByAgent, openInterestDist, shortSales / shortSalesAnalyse, brokerPositions, brokerOffers.

### Derivatives / options / warrants
optionCalculator, initialMargin / initialMarginV2, optionWarrantTree, filterRankerOptions /
filterRankerViop / filterRankerWarrants, warrantCalculator, warrantPriceStep, underlyingClassification,
minTick. → relevant for the option-IV workstream.

### Meta & calendars
| Name | URL | Params |
|------|-----|--------|
| metaData.symbols / metaDataV4.symbols | `/dumrul/v{3,4}/meta/symbols` | marketCode, exchangeId, symbolType, sectorId, submarketCode, marketId, deleted, allSymbols, symbolCode |
| meta markets / members / sectors | `/dumrul/v4/meta/{markets,members,sectors}.gz` | |
| session-hours | `/dumrul/v1/session-hours` | date |
| holidays | `/dumrul/v1/holidays` | startdate, enddate, halfday |
| economicCalendar | `/dumrul/v1/economic-calendar` | period |
| price-step-v2 | `/price-step-v2.json` | |
| indexWeight | `/dumrul/v1/index-weight` | (index constituents/weights) |
| dependency.cor | `/dumrul/v1/dependency/cor` | symbols, period (correlations) |

### Macro
tcmbData, tuikData, inflationRates, bonds, currencyPosition.

### Funds, foreign, social
`funds.*`, `fundsportfolio.*`, `fundsFlows`, `fundsVolume`; foreign-market family on
`apicloudank` (`snapshotForeignMarket`, `barForeign`, `metaForeign`, `calendarForeign`,
`priceTargetForeign`, …); `socialMedia.*` (29 endpoints), `twitter.*`.

## Notes
- Many endpoints are unverified (params from the discovery doc, not yet hit). Confirm shape before
  relying on one — capture a real response and add it to `reference/`.
- For a **pre-open brief** (settled previous close + news/KAP + calendar), REST alone (snapshot +
  bar + news + economic-calendar + session-hours) is enough — no MQTT/protobuf needed.

# 01 – Architecture overview

How the Matriks Web Trader (Angular SPA) data layer is wired, top to bottom.

```
┌─────────────────────────────────────────────────────────────────┐
│ Angular app (main-*.js)                                          │
│   ConnectionService ── tokenService.tokenProvider() ─► JWT       │
│        │  connect(brokerUrl, JWT) / subscribe(brokerUrl, topic)  │
│        ▼                                                          │
│   window.mxproxy  (thin API, mxproxy.js)                         │
│        │  postMessage over MessageChannel port                   │
│        ▼                                                          │
│   Web Worker (mxproxy-worker-1.1.11.js)   ◄── the real engine    │
│        ├── MQTT.js client (npm `mqtt`)                            │
│        ├── protobufjs + compiled schema (mxroot.messages.*)      │
│        └── WebSocket(s) to brokers                                │
│              ▼                                                    │
│   wss://rtank.radix.matriksdata.com/...   (MQTT 3.1 over WS)     │
└─────────────────────────────────────────────────────────────────┘
```

## Key components

### `mxproxy.js` (main thread, ~7 KB)
Thin façade. Public API the app uses:

| Method | Meaning |
|--------|---------|
| `connect(url, uid, password)` | Open/auth a broker. `password` may be a value **or a function** returning the current token (stored in `pwdfns`, re-pushed every 10 s via `setpassword`). |
| `subscribe(url, topic)` | Subscribe to an MQTT topic on a broker. |
| `unsubscribe(url, topic)` | Unsubscribe. |
| `disconnect(url)` / `forcedisconnect(url)` | Close. |
| `onmessage(e)` | Receives **decoded** messages: `{model:"mqtt", url, topic, payload:{…}}`. |

It spawns the worker and bridges to it over a `MessageChannel`. After the initial
`Worker.postMessage({cmd:"addclient"}, [port])` handshake, **all traffic flows over the
MessagePort**, not the worker object — important when instrumenting.

### `mxproxy-worker-*.js` (Web Worker, ~760 KB)
The real engine. Bundles:
- the `mqtt` npm client (MQTT 3.1, `MQIsdp`, keepalive, reconnect),
- `protobufjs` + a **compiled static schema** under namespace `mxroot.messages.*` (19 types),
- the WebSocket connections to brokers.

It receives `{cmd:"connect"|"subscribe"|…}` over the port, manages MQTT sessions, decodes each
PUBLISH payload via the matching protobuf type, and posts `{model:"mqtt", url, topic, payload}`
back to the main thread.

## Implication for a headless client
We don't need the browser at all. We replicate the worker's job directly in Python:
1. obtain the JWT (auth, see [03](03-auth.md)),
2. resolve broker URLs (discovery, see [04](04-discovery-brokers.md)),
3. open MQTT-over-WS with the right framing (see [02](02-transport-mqtt.md)),
4. subscribe to topics (see [05](05-topics.md)),
5. decode PUBLISH payloads with `proto/matriks.proto` (see [06](06-protobuf-schema.md)).

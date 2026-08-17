# 02 – Transport: MQTT 3.1 over WebSocket

## WebSocket handshake

```
GET wss://rtank.radix.matriksdata.com:443/market
Sec-WebSocket-Protocol: mqttv3.1        ← REQUIRED subprotocol
Origin: https://ziraatyatirim.matrikswebtrader.com   ← REQUIRED (else HTTP 403)
Sec-WebSocket-Version: 13
```
Response: `101 Switching Protocols`, `Sec-WebSocket-Protocol: mqttv3.1`.

> ⚠️ **Origin check.** The broker rejects the upgrade with **403** if `Origin` is missing or
> wrong. A browser sends it automatically; a headless client must set it explicitly. Confirmed
> on the sibling Telegram feed too — this is the #1 gotcha.

## MQTT layer

- **Protocol:** MQTT **3.1** — CONNECT carries protocol name `MQIsdp` (6 bytes) + level `0x03`.
  (Not 3.1.1 `MQTT`/level 4.) Use a client that supports 3.1, e.g. `paho-mqtt` with
  `protocol=MQTTv31`, or `gmqtt`/`aiomqtt` configured for WS transport.
- **CONNECT flags:** `0xC2` = username + password + clean-session.
- **clientId:** numeric string seen on the wire (e.g. `10688350630470`). Generate a unique one.
- **username:** constant **`JTW`** (same for every broker — it's the data-vendor account; the
  real user identity is inside the JWT).
- **password:** the **RS256 JWT** (see [03](03-auth.md)).
- **keepalive:** client sends PINGREQ periodically; broker replies PINGRESP. (On the wire these are
  the tiny 2-byte `0xC0`/`0xD0` frames.)

## Message framing on the wire (observed)
Binary WebSocket frames carrying standard MQTT packets:
- CONNECT (~584 B) → CONNACK (4 B)
- SUBSCRIBE → SUBACK
- **PUBLISH** frames: 2-byte MQTT fixed header + topic string + **protobuf payload**.
  The payload bytes are decoded with the protobuf type that matches the topic root
  (see [05](05-topics.md) and [06](06-protobuf-schema.md)).

## Brokers are separate MQTT connections
The app opens **one MQTT/WS connection per broker host+path** (market, news, stats, arf, delayed),
all with the same `uid=JTW` + JWT. Topics are then subscribed on whichever broker the discovery
doc maps them to. A headless client should mirror this: a small pool of broker connections, each
multiplexing many topic subscriptions.

## Recommended Python stack
- `websockets` (async) or `paho-mqtt`'s WebSocket transport.
- `paho-mqtt` (`MQTTv31`) or `gmqtt` for the MQTT state machine.
- `protobuf` runtime + `protoc`-compiled `matriks.proto` for payload decode.
- Decide per-topic which protobuf type to apply (topic-root → type map in [06](06-protobuf-schema.md)).

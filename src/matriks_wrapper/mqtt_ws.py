"""Minimal MQTT 3.1 over WebSocket client for the Matriks feed.

Hand-rolled because the broker negotiates the `mqttv3.1` WS subprotocol (paho hardcodes `mqtt`)
and requires a specific Origin header. We only need CONNECT / SUBSCRIBE / PINGREQ / PUBLISH(recv),
all QoS 0 — so a small implementation is simpler and more robust than bending a full client.
"""
import asyncio
import struct
import websockets


def _remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _read_remaining_length(data: bytes, i: int):
    mult = 1
    val = 0
    while True:
        b = data[i]; i += 1
        val += (b & 0x7F) * mult
        if not (b & 0x80):
            break
        mult *= 128
    return val, i


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def build_connect(client_id: str, username: str, password: str, keepalive: int = 60) -> bytes:
    # MQTT 3.1 variable header: protocol name "MQIsdp", level 3
    vh = _str("MQIsdp") + bytes([3])
    flags = 0xC2  # username + password + clean session
    vh += bytes([flags]) + struct.pack("!H", keepalive)
    payload = _str(client_id) + _str(username) + _str(password)
    body = vh + payload
    return bytes([0x10]) + _remaining_length(len(body)) + body


def build_subscribe(packet_id: int, topics, qos: int = 0) -> bytes:
    body = struct.pack("!H", packet_id)
    for t in topics:
        body += _str(t) + bytes([qos])
    return bytes([0x82]) + _remaining_length(len(body)) + body


def build_pingreq() -> bytes:
    return bytes([0xC0, 0x00])


def build_disconnect() -> bytes:
    return bytes([0xE0, 0x00])


def parse_publish(data: bytes):
    """Given a full PUBLISH packet (fixed header byte already known 0x3x), return (topic, payload)."""
    first = data[0]
    qos = (first >> 1) & 0x03
    rem, i = _read_remaining_length(data, 1)
    end = i + rem
    tlen = struct.unpack("!H", data[i:i + 2])[0]; i += 2
    topic = data[i:i + tlen].decode("utf-8"); i += tlen
    if qos > 0:
        i += 2  # packet id
    payload = data[i:end]
    return topic, payload


class MqttWsClient:
    def __init__(self, ws_url, origin, client_id, username, password, keepalive=60):
        self.ws_url = ws_url
        self.origin = origin
        self.client_id = client_id
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self.ws = None
        self._pid = 0
        self._buf = bytearray()

    async def connect(self):
        self.ws = await websockets.connect(
            self.ws_url,
            origin=self.origin,
            subprotocols=["mqttv3.1"],
            max_size=None,
            open_timeout=15,
            # MQTT-over-WS: kütüphanenin otomatik WS-PING'ini KAPAT. Sunucu WS ping frame'lerine
            # PONG dönmüyor → varsayılan ping_interval=20s, ~30s'de 1005 ile koparıyordu. Keepalive'ı
            # MQTT PINGREQ ile kendimiz yapıyoruz (ping()).
            ping_interval=None,
            ping_timeout=None,
            user_agent_header="Mozilla/5.0 (Windows NT 10.0; Win64; x64) matriks-wrapper",
        )
        await self.ws.send(build_connect(self.client_id, self.username, self.password, self.keepalive))
        # await CONNACK
        pkt = await self._next_packet(timeout=15)
        if not pkt or pkt[0] >> 4 != 2:
            raise RuntimeError(f"no CONNACK, got {pkt[:4] if pkt else None!r}")
        code = pkt[3] if len(pkt) >= 4 else -1
        if code != 0:
            raise RuntimeError(f"CONNACK refused, return code {code}")
        return True

    async def subscribe(self, topics, qos=0):
        self._pid = (self._pid + 1) & 0xFFFF
        await self.ws.send(build_subscribe(self._pid, topics, qos))

    async def ping(self):
        await self.ws.send(build_pingreq())

    async def _next_packet(self, timeout=None):
        """Reassemble one MQTT packet from the WS byte stream."""
        while True:
            pkt = self._try_extract()
            if pkt is not None:
                return pkt
            try:
                frame = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            except websockets.ConnectionClosed:
                return None
            if isinstance(frame, str):
                frame = frame.encode()
            self._buf.extend(frame)

    def _try_extract(self):
        if len(self._buf) < 2:
            return None
        # decode remaining length
        mult = 1; val = 0; i = 1
        while True:
            if i >= len(self._buf):
                return None
            b = self._buf[i]; i += 1
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
        total = i + val
        if len(self._buf) < total:
            return None
        pkt = bytes(self._buf[:total])
        del self._buf[:total]
        return pkt

    async def messages(self, idle_timeout=None):
        """Yield (topic, payload) for each PUBLISH; auto-answer keepalive."""
        last_ping = asyncio.get_event_loop().time()
        while True:
            pkt = await self._next_packet(timeout=1)
            now = asyncio.get_event_loop().time()
            if now - last_ping >= self.keepalive / 2:
                await self.ping(); last_ping = now
            if pkt is None:
                continue
            ptype = pkt[0] >> 4
            if ptype == 3:  # PUBLISH
                yield parse_publish(pkt)
            # ptype 9 SUBACK, 13 PINGRESP, etc. ignored

    async def close(self):
        try:
            if self.ws:
                await self.ws.send(build_disconnect())
                await self.ws.close()
        except Exception:
            pass

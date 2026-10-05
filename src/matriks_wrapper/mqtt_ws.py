"""Minimal MQTT 3.1 over WebSocket client for the Matriks feed.

Hand-rolled because the broker negotiates the `mqttv3.1` WS subprotocol (paho hardcodes `mqtt`)
and requires a specific Origin header. We only need CONNECT / SUBSCRIBE / UNSUBSCRIBE / PINGREQ /
PUBLISH(recv), all QoS 0 — so a small implementation is simpler and more robust than bending a
full client.
"""
import asyncio
import struct
import time

import websockets

#: SUBACK return code for a refused topic (MQTT 3.1.1; 3.1 brokers only send granted QoS 0-2).
SUBACK_FAILURE = 0x80


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


def build_unsubscribe(packet_id: int, topics) -> bytes:
    body = struct.pack("!H", packet_id)
    for t in topics:
        body += _str(t)
    return bytes([0xA2]) + _remaining_length(len(body)) + body


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


def parse_suback(data: bytes):
    """SUBACK -> (packet_id, [return_code per topic, in SUBSCRIBE order])."""
    rem, i = _read_remaining_length(data, 1)
    end = i + rem
    pid = struct.unpack("!H", data[i:i + 2])[0]
    return pid, list(data[i + 2:end])


def parse_unsuback(data: bytes):
    rem, i = _read_remaining_length(data, 1)
    return struct.unpack("!H", data[i:i + 2])[0]


class MqttWsClient:
    """One MQTT-over-WS session.

    `on_suback(pid, codes)` / `on_unsuback(pid)` are optional callbacks invoked from `messages()`.
    Liveness: a PINGREQ goes out every keepalive/2 s; if *nothing* (PUBLISH, PINGRESP, SUBACK...)
    arrives for `dead_after` seconds the socket is closed so the caller reconnects — a half-open
    TCP connection would otherwise wait forever.
    """

    def __init__(self, ws_url, origin, client_id, username, password, keepalive=60,
                 on_suback=None, on_unsuback=None, dead_after=None):
        self.ws_url = ws_url
        self.origin = origin
        self.client_id = client_id
        self.username = username
        self.password = password
        self.keepalive = keepalive
        self.on_suback = on_suback
        self.on_unsuback = on_unsuback
        self.dead_after = dead_after or max(300, 5 * keepalive)
        self.ws = None
        self.pingresp = 0           # alınan PINGRESP sayısı (tanı: sunucu keepalive'a yanıt veriyor mu)
        self._pid = 0
        self._buf = bytearray()
        self.last_rx = 0.0          # monotonic time of the last received packet

    async def connect(self):
        self.ws = await websockets.connect(
            self.ws_url,
            origin=self.origin,
            subprotocols=["mqttv3.1"],
            max_size=None,
            open_timeout=15,
            # MQTT-over-WS: kütüphanenin otomatik WS-PING'ini KAPAT. Sunucu WS ping frame'lerine
            # PONG dönmüyor → varsayılan ping_interval=20s, ~30s'de 1005 ile koparıyordu. Keepalive'ı
            # MQTT PINGREQ ile kendimiz yapıyoruz (_keepalive()).
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
        self.last_rx = time.monotonic()
        return True

    def _next_pid(self):
        self._pid = (self._pid % 0xFFFF) + 1      # 1..65535 (0 is not a valid packet id)
        return self._pid

    async def subscribe(self, topics, qos=0):
        """Send one SUBSCRIBE; returns its packet id (match it in on_suback)."""
        pid = self._next_pid()
        await self.ws.send(build_subscribe(pid, topics, qos))
        return pid

    async def unsubscribe(self, topics):
        pid = self._next_pid()
        await self.ws.send(build_unsubscribe(pid, topics))
        return pid

    async def ping(self):
        await self.ws.send(build_pingreq())

    async def _next_packet(self, timeout=None):
        """Reassemble one MQTT packet from the WS byte stream (used for CONNACK only)."""
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
        buf = self._buf
        n = len(buf)
        if n < 2:
            return None
        # decode remaining length
        mult = 1; val = 0; i = 1
        while True:
            if i >= n:
                return None
            b = buf[i]; i += 1
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
        total = i + val
        if n < total:
            return None
        if n == total:              # tipik durum: bir WS çerçevesi = bir MQTT paketi (kopyasız)
            pkt = bytes(buf)
            buf.clear()
            return pkt
        pkt = bytes(buf[:total])
        del buf[:total]
        return pkt

    async def _keepalive(self):
        interval = max(1.0, self.keepalive / 2)
        while True:
            await asyncio.sleep(interval)
            if time.monotonic() - self.last_rx > self.dead_after:
                # Yarı açık bağlantı: hiçbir paket gelmiyor → kapat, okuyucu biter, üst katman yeniden bağlanır.
                await self.ws.close()
                return
            try:
                await self.ping()
            except Exception:
                return

    async def messages(self):
        """Yield (topic, payload) for each PUBLISH; SUBACK/UNSUBACK go to the callbacks.

        Raises ConnectionError when the socket closes (cleanly or not) so the caller reconnects
        immediately — the old loop swallowed ConnectionClosed and spun on recv() until the next
        PINGREQ failed (up to keepalive/2 s of busy loop)."""
        ka = asyncio.ensure_future(self._keepalive())
        try:
            # Bağlanırken tamponda kalmış paketler (CONNACK'ten sonra gelenler) önce işlenir.
            while True:
                pkt = self._try_extract()
                if pkt is None:
                    break
                out = self._dispatch(pkt)
                if out is not None:
                    yield out
            async for frame in self.ws:
                self.last_rx = time.monotonic()
                if isinstance(frame, str):
                    frame = frame.encode()
                self._buf.extend(frame)
                while True:
                    pkt = self._try_extract()
                    if pkt is None:
                        break
                    out = self._dispatch(pkt)
                    if out is not None:
                        yield out
            raise ConnectionError(f"websocket closed ({getattr(self.ws, 'close_code', None)})")
        except websockets.ConnectionClosed as e:
            raise ConnectionError(f"websocket closed ({e!r})") from None
        finally:
            ka.cancel()

    def _dispatch(self, pkt):
        ptype = pkt[0] >> 4
        if ptype == 3:  # PUBLISH
            return parse_publish(pkt)
        if ptype == 9:  # SUBACK
            if self.on_suback is not None:
                pid, codes = parse_suback(pkt)
                self.on_suback(pid, codes)
        elif ptype == 11:  # UNSUBACK
            if self.on_unsuback is not None:
                self.on_unsuback(parse_unsuback(pkt))
        elif ptype == 13:  # PINGRESP: last_rx zaten güncellendi (canlılık)
            self.pingresp += 1
        return None

    async def close(self):
        try:
            if self.ws:
                await self.ws.send(build_disconnect())
                await self.ws.close()
        except Exception:
            pass

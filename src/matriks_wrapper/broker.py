"""BrokerPool: one MQTT-over-WS connection per broker, multiplexing many topic subscriptions,
with auto-reconnect, refreshable token, SUBACK tracking and incremental (un)subscribe."""
import asyncio
import time

from . import config
from .mqtt_ws import MqttWsClient, SUBACK_FAILURE

SUB_BATCH = 150     # topics per SUBSCRIBE packet
YIELD_EVERY = 256   # mesaj: tampon doluyken bile olay döngüsüne (flush, keepalive, ölçüm) sıra ver
ACK_TIMEOUT = 30    # sn: bu süreyi aşan yanıtsız SUBSCRIBE "no_ack" sayılır


class BrokerConn:
    def __init__(self, url, topics, token_provider, on_message, name):
        self.url = url
        self.topics = list(dict.fromkeys(topics))   # istenen konular (tekil, sıra korunur)
        self.token_provider = token_provider
        self.on_message = on_message
        self.name = name
        self.client = None
        self._stop = False
        self.connected = False
        self.connects = 0
        self.connected_since = None
        self.last_error = None
        self.messages = 0
        self._sub_lock = asyncio.Lock()
        self._subscribed = set()     # bu oturumda SUBSCRIBE gönderilmiş konular
        self.sub_state = {}          # topic -> 'pending' | 'ok' | 'fail'   (SUBACK dönüş kodu)
        self._pending = {}           # pid -> (topics, gönderim anı)
        self._err_logged = 0.0

    # --- SUBACK / UNSUBACK ---
    def _on_suback(self, pid, codes):
        ent = self._pending.pop(pid, None)
        if ent is None:
            return
        topics, _sent = ent
        for t, c in zip(topics, codes):
            if t in self._subscribed:
                self.sub_state[t] = 'fail' if c >= SUBACK_FAILURE else 'ok'

    def _on_unsuback(self, pid):
        self._pending.pop(pid, None)

    async def _send_subscribe(self, topics):
        for i in range(0, len(topics), SUB_BATCH):
            chunk = topics[i:i + SUB_BATCH]
            for t in chunk:
                self._subscribed.add(t)
                self.sub_state[t] = 'pending'
            pid = await self.client.subscribe(chunk)
            self._pending[pid] = (chunk, time.monotonic())
            await asyncio.sleep(0.05)

    async def _send_unsubscribe(self, topics):
        for i in range(0, len(topics), SUB_BATCH):
            chunk = topics[i:i + SUB_BATCH]
            for t in chunk:
                self._subscribed.discard(t)
                self.sub_state.pop(t, None)
            pid = await self.client.unsubscribe(chunk)
            self._pending[pid] = ([], time.monotonic())
            await asyncio.sleep(0.05)

    async def _initial_subscribe(self):
        async with self._sub_lock:
            await self._send_subscribe(list(self.topics))

    async def update_topics(self, topics):
        """İstenen konu kümesini değiştir. Bağlıysa YALNIZ farkı (un)subscribe eder — bağlantı ve
        diğer abonelikler korunur. Bağlı değilse yeni liste bir sonraki bağlantıda kullanılır.
        Döner: (eklenen, çıkarılan) konu sayısı."""
        new = list(dict.fromkeys(topics))
        new_set = set(new)
        async with self._sub_lock:
            self.topics = new
            if not (self.connected and self.client is not None):
                return 0, 0
            added = [t for t in new if t not in self._subscribed]
            removed = [t for t in self._subscribed if t not in new_set]
            if removed:
                await self._send_unsubscribe(removed)
            if added:
                await self._send_subscribe(added)
            return len(added), len(removed)

    def sub_summary(self):
        now = time.monotonic()
        ok = fail = pending = no_ack = 0
        late = set()
        for topics, sent in self._pending.values():
            if now - sent > ACK_TIMEOUT:
                late.update(topics)
        failed = []
        for t, st in self.sub_state.items():
            if st == 'ok':
                ok += 1
            elif st == 'fail':
                fail += 1
                if len(failed) < 20:
                    failed.append(t)
            elif t in late:
                no_ack += 1
            else:
                pending += 1
        return {'want': len(self.topics), 'subscribed': len(self._subscribed), 'ok': ok, 'fail': fail,
                'pending': pending, 'no_ack': no_ack, 'failed_examples': failed}

    async def run(self):
        backoff = 1
        loop = asyncio.get_running_loop()
        while not self._stop:
            sub_task = None
            try:
                # Token üretimi (C6) senkron HTTP: olay döngüsünü kilitlemesin diye iş parçacığında.
                token = await loop.run_in_executor(None, self.token_provider)
                self.client = MqttWsClient(
                    self.url, config.ORIGIN,
                    client_id=f"mtxwrap-{self.name}-{int(time.time())}",
                    username=config.MQTT_USERNAME,
                    password=token,
                    on_suback=self._on_suback,
                    on_unsuback=self._on_unsuback,
                )
                await self.client.connect()
                self._subscribed = set()
                self.sub_state = {}
                self._pending = {}
                self.connected = True
                self.connects += 1
                self.connected_since = time.time()
                backoff = 1
                # Abonelik okumayla EŞZAMANLI: 10 bin+ konuda ilk anlık görüntü seli beklerken tampon şişmesin.
                sub_task = asyncio.ensure_future(self._initial_subscribe())
                n = 0
                on_message = self.on_message
                async for topic, payload in self.client.messages():
                    try:
                        on_message(topic, payload)
                    except Exception as e:
                        if time.monotonic() - self._err_logged > 10:
                            self._err_logged = time.monotonic()
                            print(f"[{self.name}] on_message error: {e!r}")
                    n += 1
                    if n >= YIELD_EVERY:
                        self.messages += n
                        n = 0
                        await asyncio.sleep(0)
                self.messages += n
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                self.last_error = repr(e)
                if self._stop:
                    break
                print(f"[{self.name}] disconnected ({e!r}); reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                self.connected = False
                if sub_task is not None:
                    sub_task.cancel()
                if self.client:
                    await self.client.close()

    async def stop(self):
        self._stop = True
        if self.client:
            await self.client.close()


class BrokerPool:
    def __init__(self, token_provider, on_message):
        self.token_provider = token_provider
        self.on_message = on_message
        self.conns = []
        self.tasks = []

    def add_broker(self, url, topics, name):
        conn = BrokerConn(url, topics, self.token_provider, self.on_message, name)
        self.conns.append(conn)
        return conn

    def get(self, url):
        for c in self.conns:
            if c.url == url:
                return c
        return None

    async def start(self):
        self.tasks = [asyncio.ensure_future(c.run()) for c in self.conns]

    def start_conn(self, conn):
        """Havuz çalışırken eklenen yeni broker bağlantısını başlat."""
        self.tasks.append(asyncio.ensure_future(conn.run()))

    async def stop(self):
        for c in self.conns:
            await c.stop()
        for t in self.tasks:
            t.cancel()

    def status(self):
        return {c.name: ("up" if c.connected else "down") for c in self.conns}

    def sub_summary(self):
        tot = {'want': 0, 'subscribed': 0, 'ok': 0, 'fail': 0, 'pending': 0, 'no_ack': 0, 'failed_examples': []}
        for c in self.conns:
            s = c.sub_summary()
            for k in ('want', 'subscribed', 'ok', 'fail', 'pending', 'no_ack'):
                tot[k] += s[k]
            tot['failed_examples'].extend(s['failed_examples'][:20 - len(tot['failed_examples'])])
        return tot

    def subscribed_topics(self):
        out = set()
        for c in self.conns:
            out |= c._subscribed
        return out

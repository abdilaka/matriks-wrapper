"""Supervisor: keeps the live data service running with a valid token, and triggers a remote
re-login (captcha+OTP via Telegram) when the Ziraat session dies.

Detection is hybrid:
  • Proactive periodic: every tick, refresh the JWT before expiry (AuthManager). If the mint is
    REJECTED (SessionExpired) the session is dead -> relogin.
  • Reactive on broker health: a mid-life session kill (e.g. another login elsewhere) leaves the
    cached JWT TTL-valid but the brokers get rejected/drop. If no broker has been up for a while,
    force a fresh mint; if that is rejected -> relogin.

Re-login coordination (single active MQTT session per account): stop the broker pool, run the
remote login, mint a fresh token, then rebuild the pool.

Watchlist changes are applied INCREMENTALLY on the live connection (only the difference is
(un)subscribed); a failed change never tears the pool down — it is retried on the next health tick.

Run this instead of service.py for unattended operation:
    python -u src/supervisor.py
"""
import asyncio
import time
from collections import defaultdict


from . import config
from . import discovery
from .decode import decode as decode_msg  # NOT `from . import decode` — __init__ shadows it with the func
from .broker import BrokerPool
from .store import FileTickStore
from .auth import AuthManager, SessionExpired, load_session
from .login import remote_login
from .telegram_relay import TelegramRelay

CHECK_EVERY = 30          # health tick seconds
BROKER_DOWN_GRACE = 90    # seconds with no broker up before forcing a refresh
RELOGIN_DEBOUNCE = 120    # min seconds between relogin attempts


class Supervisor:
    def __init__(self, store=None, on_update=None, watchlist=None, headless=None):
        self.auth = AuthManager()
        self.store = store if store is not None else FileTickStore()
        self.on_update = on_update          # optional extra callback(symbol, root, data)
        self.watchlist = watchlist          # dict, yaml path, or None (-> config.load_watchlist)
        self.headless = headless
        self.pool = None
        self._stop_event = None
        self.last_healthy = time.time()
        self.last_relogin = 0.0
        self._bmap = None                   # topic-prefix -> broker URL (son başarılı disco)
        self.topic_meta = {}                # topic -> (symbol, kind) — abone↔veri izlemesi için
        self._resub_pending = None          # başarısız watchlist değişikliği (health_tick'te tekrar)
        self._resub_lock = None
        try:
            self.relay = TelegramRelay()
        except Exception:
            self.relay = None

    # --- helpers ---
    def notify(self, msg):
        print(msg)
        if self.relay:
            # Telegram HTTP çağrısı olay döngüsünü kilitlemesin: iş parçacığında, ateşle-unut.
            try:
                asyncio.get_running_loop().run_in_executor(None, self._send_relay, msg)
            except RuntimeError:
                self._send_relay(msg)

    def _send_relay(self, msg):
        try:
            self.relay.send_text(msg)
        except Exception:
            pass

    def token_provider(self):
        return self.auth.current_token()

    def _watchlist(self):
        wl = self.watchlist
        if wl is None or isinstance(wl, str):
            return config.load_watchlist(wl)
        return wl

    def on_message(self, topic, payload):
        _rt, root, sym, data = decode_msg(topic, payload)
        self.store.update(sym, root, data)
        if self.on_update:
            try: self.on_update(sym, root, data)
            except Exception as e: print(f"[on_update] {e!r}")

    async def _ensure_bmap(self, refresh=False):
        if self._bmap is None or refresh:
            loop = asyncio.get_running_loop()
            disco = await loop.run_in_executor(None, discovery.fetch_disco)
            self._bmap = discovery.broker_map(disco, tier="rt")
        return self._bmap

    def _plan(self, wl, bmap):
        """watchlist -> {broker_url: [topics]} ve topic_meta."""
        by_broker = defaultdict(list)
        meta = {}
        unresolved = 0
        for topic, sym, kind in config.topics_for(wl):
            url = discovery.resolve_topic_broker(bmap, topic)
            if url:
                by_broker[url].append(topic)
                meta[topic] = (sym, kind)
            else:
                unresolved += 1
        market = bmap.get("mx/symbol")
        if market:
            by_broker[market].append("mx/timestamp")
        if unresolved:
            print(f"[plan] {unresolved} konu için broker bulunamadı")
        return by_broker, meta

    async def build_pool(self):
        bmap = await self._ensure_bmap(refresh=True)
        by_broker, meta = self._plan(self._watchlist(), bmap)
        pool = BrokerPool(self.token_provider, self.on_message)
        for i, (url, topics) in enumerate(by_broker.items()):
            pool.add_broker(url, topics, name=f"b{i}")
        await pool.start()
        self.topic_meta = meta
        return pool

    async def relogin(self, reason):
        if time.time() - self.last_relogin < RELOGIN_DEBOUNCE:
            return
        self.last_relogin = time.time()
        self.notify(f"🔁 Session yenileniyor ({reason}). Telegram'dan captcha+OTP gelecek.")
        if self.pool:
            await self.pool.stop(); self.pool = None
        ok = await remote_login(self.relay, headless=self.headless)
        if not ok:
            self.notify("⛔ Re-login başarısız. Bir sonraki kontrolde tekrar denenecek.")
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.auth.refresh)   # fresh token from the new session
        self.pool = await self.build_pool()
        self.last_healthy = time.time()
        self.notify(f"✅ Servis yeniden bağlandı. Lisanslar: "
                    f"{[l.get('LicenseCode') for l in self.auth.licences]}")

    async def resubscribe(self, new_watchlist):
        """Watchlist değişince YALNIZ farkı (un)subscribe et: bağlantı ve token korunur, re-login YOK.
        Hata olursa mevcut havuz AYAKTA kalır ve değişiklik bir sonraki health_tick'te tekrar denenir
        (eski davranış havuzu durdurup kuramazsa None bırakıyordu → feed sessizce ölüyordu)."""
        self.watchlist = new_watchlist
        if self._resub_lock is None:
            self._resub_lock = asyncio.Lock()
        async with self._resub_lock:
            try:
                if self.pool is None:
                    self.pool = await self.build_pool()
                    self.last_healthy = time.time()
                    self._resub_pending = None
                    print("🔄 Watchlist: havuz kuruldu.")
                    return
                bmap = await self._ensure_bmap()
                by_broker, meta = self._plan(new_watchlist, bmap)
                added = removed = 0
                for url, topics in by_broker.items():
                    conn = self.pool.get(url)
                    if conn is None:
                        conn = self.pool.add_broker(url, topics, name=f"b{len(self.pool.conns)}")
                        self.pool.start_conn(conn)
                        added += len(topics)
                    else:
                        a, r = await conn.update_topics(topics)
                        added += a; removed += r
                for conn in list(self.pool.conns):
                    if conn.url not in by_broker:
                        a, r = await conn.update_topics([])
                        removed += r
                self.topic_meta = meta
                self._resub_pending = None
                print(f"🔄 Watchlist güncellendi: +{added} / -{removed} konu (bağlantı korunarak).")
            except Exception as e:  # noqa
                self._resub_pending = new_watchlist
                self.notify(f"⚠️ Resubscribe hatası: {e!r} — mevcut abonelikler korunuyor, tekrar denenecek.")

    async def ensure_session(self):
        """At startup: log in if there is no session, or if minting is already rejected.
        Geçici ağ hatasında birkaç kez dener; yine olmazsa devam eder (bağlantılar token'ı
        her denemede yeniden ister) — süreç sessizce ölmesin."""
        loop = asyncio.get_running_loop()
        delay = 2
        for _ in range(6):
            try:
                load_session()
                await loop.run_in_executor(None, self.auth.current_token)
                return
            except (FileNotFoundError, SessionExpired):
                await self.relogin("ilk kurulum / oturum yok")
                return
            except Exception as e:
                print(f"[startup] token alınamadı (geçici?): {e!r}; {delay}s sonra tekrar")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def health_tick(self):
        loop = asyncio.get_running_loop()
        # 1) proactive refresh before expiry; rejection => session dead
        try:
            await loop.run_in_executor(None, self.auth.current_token)
        except SessionExpired:
            await self.relogin("token reddedildi"); return
        except Exception as e:
            print(f"[health] geçici token hatası: {e!r}")   # network; retry next tick
        # 2) havuz yoksa (önceki kurulum/relogin yarıda kaldı) sessizce bekleme — yeniden kur
        if self.pool is None:
            try:
                self.pool = await self.build_pool()
                self.last_healthy = time.time()
                self.notify("🔁 Matriks havuzu yeniden kuruldu (önceki kurulum başarısızdı).")
            except Exception as e:
                print(f"[health] havuz kurulamadı: {e!r}; sonraki kontrolde tekrar")
            return
        if self._resub_pending is not None:
            await self.resubscribe(self._resub_pending)
        # 3) broker health -> detect mid-life session kill
        if any(s == "up" for s in self.pool.status().values()):
            self.last_healthy = time.time()
        elif time.time() - self.last_healthy > BROKER_DOWN_GRACE:
            try:
                await loop.run_in_executor(None, self.auth.refresh)   # force a fresh mint
                self.last_healthy = time.time()
                print("[health] brokerlar düşmüştü; token tazelendi, reconnect bekleniyor")
            except SessionExpired:
                await self.relogin("brokerlar düştü + token reddedildi")
            except Exception as e:
                print(f"[health] token tazelenemedi (geçici?): {e!r}")

    def request_stop(self):
        """Signal the run loop to stop (thread-safe via the loop's call_soon_threadsafe)."""
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()

    def status_line(self):
        st = self.store.stats()
        line = (f"brokers={self.pool.status() if self.pool else {}} token_ttl={self.auth.ttl()}s "
                f"symbols={st['symbols']} updates={st['updates']}")
        if self.pool:
            s = self.pool.sub_summary()
            line += f" subs=ok:{s['ok']}/fail:{s['fail']}/pending:{s['pending'] + s['no_ack']}/want:{s['want']}"
        return line

    async def run(self):
        self._stop_event = asyncio.Event()
        await self.ensure_session()
        if self.pool is None:
            try:
                self.pool = await self.build_pool()
            except Exception as e:
                print(f"[startup] havuz kurulamadı: {e!r}; health_tick tekrar deneyecek")
        # Store'un arka plan görevi (toplu yazım/ölçüm) varsa aynı olay döngüsünde başlat.
        bg = asyncio.ensure_future(self.store.run(self)) if hasattr(self.store, "run") else None
        self.notify("📡 Matriks listener çalışıyor (supervisor).")
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_EVERY)
                    break  # stop requested
                except asyncio.TimeoutError:
                    pass
                await self.health_tick()
                self.store.flush()
                print(f"[{time.strftime('%H:%M:%S')}] {self.status_line()}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            if bg is not None:
                bg.cancel()
            if self.pool:
                await self.pool.stop()
            self.store.close()
            self.notify("🛑 Supervisor durduruldu.")


def main():
    """Console entry point: `matriks-run` / `python -m matriks_wrapper.supervisor`."""
    try:
        asyncio.run(Supervisor().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

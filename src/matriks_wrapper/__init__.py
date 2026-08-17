"""matriks_wrapper — a small, robust client for the Ziraat Yatırım Matriks Web Trader feed.

Public API
----------
High-level (recommended for embedding in your own app):

    from matriks_wrapper import MatriksFeed

    feed = MatriksFeed(watchlist="watchlist.yaml")   # or a dict
    feed.start_in_thread()                            # non-blocking; logs in via Telegram if needed
    ...
    px = feed.snapshot("X30YVADE")                    # {'last':..., 'settlement':..., ...}

Low-level building blocks are also exported (AuthManager, Supervisor, decoders, stores) for when
you need finer control. One process, one entry point — `Supervisor` orchestrates auth + listener +
re-login; you don't run the parts separately.
"""
import asyncio
import threading

from . import config
from .auth import AuthManager, SessionExpired, mint_token
from .store import MemoryTickStore, FileTickStore
from .decode import decode, TOPIC_TYPE
from .supervisor import Supervisor
from .login import remote_login
from .symbols import fetch_symbols, normalize as normalize_symbols

__version__ = "0.1.0"

__all__ = [
    "MatriksFeed", "Supervisor", "AuthManager", "SessionExpired", "mint_token",
    "MemoryTickStore", "FileTickStore", "decode", "remote_login", "config",
    "fetch_symbols", "normalize_symbols",
]


class MatriksFeed:
    """Thread-friendly facade over the supervised live feed.

    Parameters
    ----------
    watchlist : dict | str | None
        A dict ``{indices:[...], fx:[...], equities:[...], futures:[...], options:[...]}``, a path
        to a ``watchlist.yaml``, or ``None`` to load ``./watchlist.yaml``.
    on_update : callable | None
        Optional ``fn(symbol, root, data)`` invoked on every tick (after the store is updated).
    store : object | None
        Tick store. Defaults to :class:`MemoryTickStore` (live snapshot only, no files). Pass
        :class:`FileTickStore` to also persist JSONL, or your own (e.g. a Redis store) with the same
        ``update/snapshot/stats/flush/close`` interface.
    headless : bool
        Run the login browser headless (default True). Set False to watch it locally.
    """

    def __init__(self, watchlist=None, on_update=None, store=None, headless=True):
        self._sup = Supervisor(
            store=store if store is not None else MemoryTickStore(),
            on_update=on_update, watchlist=watchlist, headless=headless,
        )
        self._thread = None
        self._loop = None

    # --- live data access (call from your app / any thread) ---
    @property
    def store(self):
        return self._sup.store

    def snapshot(self, symbol):
        """Latest merged state for one symbol, or None if not seen yet."""
        return self._sup.store.snapshot(symbol)

    def snapshots(self):
        """Shallow copy of all current per-symbol snapshots."""
        return dict(self._sup.store.snap)

    def stats(self):
        return self._sup.store.stats()

    @property
    def licences(self):
        return self._sup.auth.licences

    # --- lifecycle ---
    async def start(self):
        """Run the feed on the current event loop (blocks until stopped)."""
        await self._sup.run()

    def start_in_thread(self):
        """Start the feed in a background daemon thread and return immediately."""
        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._sup.run())
            except Exception as e:  # noqa
                print("[MatriksFeed] stopped:", repr(e))
        self._thread = threading.Thread(target=_run, daemon=True, name="matriks-feed")
        self._thread.start()
        return self

    def set_watchlist(self, watchlist):
        """Canlı feed'i durdurmadan watchlist'i değiştir; thread-safe (resubscribe planlar).
        Token korunur, re-login olmaz. `watchlist` dict ya da yaml yolu olabilir."""
        if self._loop is None:
            self._sup.watchlist = watchlist
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._sup.resubscribe(watchlist))
        )

    def stop(self, timeout=10):
        """Request a clean stop and join the background thread (if any)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._sup.request_stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

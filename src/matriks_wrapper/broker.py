"""BrokerPool: one MQTT-over-WS connection per broker, multiplexing many topic subscriptions,
with auto-reconnect and refreshable token."""
import asyncio
import time

from . import config
from .mqtt_ws import MqttWsClient

SUB_BATCH = 150  # topics per SUBSCRIBE packet


class BrokerConn:
    def __init__(self, url, topics, token_provider, on_message, name):
        self.url = url
        self.topics = topics            # list[str]
        self.token_provider = token_provider
        self.on_message = on_message
        self.name = name
        self.client = None
        self._stop = False
        self.connected = False

    async def run(self):
        backoff = 1
        while not self._stop:
            try:
                self.client = MqttWsClient(
                    self.url, config.ORIGIN,
                    client_id=f"mtxwrap-{self.name}-{int(time.time())}",
                    username=config.MQTT_USERNAME,
                    password=self.token_provider(),
                )
                await self.client.connect()
                self.connected = True
                backoff = 1
                for i in range(0, len(self.topics), SUB_BATCH):
                    await self.client.subscribe(self.topics[i:i + SUB_BATCH])
                    await asyncio.sleep(0.05)
                async for topic, payload in self.client.messages():
                    try:
                        self.on_message(topic, payload)
                    except Exception as e:
                        print(f"[{self.name}] on_message error: {e!r}")
            except Exception as e:
                self.connected = False
                if self._stop:
                    break
                print(f"[{self.name}] disconnected ({e!r}); reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
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
        self.conns.append(BrokerConn(url, topics, self.token_provider, self.on_message, name))

    async def start(self):
        self.tasks = [asyncio.create_task(c.run()) for c in self.conns]

    async def stop(self):
        for c in self.conns:
            await c.stop()
        for t in self.tasks:
            t.cancel()

    def status(self):
        return {c.name: ("up" if c.connected else "down") for c in self.conns}

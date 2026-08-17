# matriks-wrapper

> **Sorumluluk Reddi / Disclaimer**
>
> Bu kütüphane yalnızca kişisel kullanım ve eğitim amaçlıdır. Resmi bir API değildir; Matriks
> Bilgi Dağıtım Hizmetleri veya Ziraat Yatırım ile bir ilişkisi ya da onayı yoktur. Erişilen
> veriler Borsa İstanbul ve ilgili veri sağlayıcısına aittir. Ticari kullanım için Borsa İstanbul
> ve veri sağlayıcısından izin almanız gerekebilir. Kütüphaneyi yalnızca kendi lisanslı hesabınızla
> kullanın.
>
> This library is for personal and educational use only. It is not an official API and has no
> affiliation with or endorsement from Matriks Bilgi Dağıtım Hizmetleri or Ziraat Yatırım. The data
> accessed belongs to Borsa Istanbul and the relevant data vendor. Commercial use may require
> permission from Borsa Istanbul and the data vendor. Use it only with your own licensed account.

Ziraat Yatırım Matriks Web Trader için Python veri kütüphanesi. Giriş, sembol listesi, canlı fiyat
akışı ve geçmiş OHLC verisi.

## Kurulum

```bash
pip install git+https://github.com/abdilaka/matriks-wrapper
playwright install chromium     # giriş akışı için
                                # Linux sunucuda ayrıca: playwright install-deps
```

Kimlik bilgileri `.env` dosyasından okunur:

```bash
cp .env.example .env
```

| Değişken | Açıklama |
|---|---|
| `ZRY_CUSTOMER_NO` | Ziraat Yatırım müşteri numarası |
| `ZRY_PASSWORD` | Ziraat Yatırım parolası |
| `TG_BOT_TOKEN` | Telegram bot token'ı ([@BotFather](https://t.me/BotFather)) |
| `TG_CHAT_ID` | Captcha ve OTP'nin gönderileceği Telegram chat id |

Çalışma dosyaları (`.env`, `.secrets/`, `watchlist.yaml`, `data/`) bulunulan dizinde tutulur.
Farklı bir konum için `MTX_HOME` ortam değişkeni kullanılır. Kurulu paketin içine kimlik bilgisi
yazılmaz.

## Hızlı Başlangıç

İlk kullanımda bir kez giriş yapılması gerekir. Sonraki çağrılar mevcut oturumu kullanır:

```bash
matriks-login
```

```python
from matriks_wrapper import MatriksFeed, fetch_symbols, normalize_symbols, mint_token
from matriks_wrapper.history import get_bars

# Token (captcha/OTP sormaz, mevcut oturumu kullanır)
token, lisanslar = mint_token()

# Sembol listesi
kayitlar = normalize_symbols(fetch_symbols())
opsiyonlar = [k for k in kayitlar if k["type"] == "option" and k["underlying"] == "GARAN"]

# Geçmiş veri
bars = get_bars("GARAN", "1day", count=300)

# Canlı veri
feed = MatriksFeed(watchlist={"indices": ["XU030"], "equities": ["GARAN", "THYAO"]})
feed.start_in_thread()
print(feed.snapshot("GARAN"))       # {'last':…, 'bid':…, 'ask':…, 'volume':…}
feed.stop()
```

## Giriş ve Token

Giriş iki aşamalıdır. İlk kurulumda bir kez uzaktan giriş yapılır: Playwright tarayıcıyı açar,
captcha görselini Telegram'a gönderir, cevap alındıktan sonra SMS ile gelen OTP'yi sorar. Başarılı
girişte oturum bilgisi `.secrets/` altına yazılır.

Sonraki tüm token'lar captcha ve OTP sorulmadan üretilir. Token ömrü 5 saattir.

```python
from matriks_wrapper import mint_token, remote_login
from matriks_wrapper.auth import jwt_ttl
import asyncio

# Uzaktan giriş (yalnızca oturum yoksa gerekir)
asyncio.run(remote_login())

# Taze token
token, lisanslar = mint_token()
print(jwt_ttl(token) // 60, "dakika kaldı")
```

> **Not**: `MatriksFeed` ve `matriks-run` giriş, token tazeleme ve oturum düştüğünde yeniden
> bağlanma işlemlerini kendisi yürütür. Yukarıdaki çağrılar elle yapılmak zorunda değildir.

> **Not**: Hesap başına tek MQTT oturumu vardır. Dinleyici çalışırken tarayıcıdan Matriks Web
> Trader'a giriş yapılırsa oturumlardan biri düşer.

## Canlı Veri

`MatriksFeed` arka planda bir thread içinde çalışır; abonelik, protobuf çözümü ve token yönetimi
kütüphane tarafında halledilir.

```python
from matriks_wrapper import MatriksFeed, FileTickStore

watchlist = {
    "indices": ["XU030", "XU100"],
    "equities": ["GARAN", "THYAO", "ASELS"],
    "futures": ["F_XU0300826"],
}

feed = MatriksFeed(watchlist=watchlist)
feed.start_in_thread()                      # bloklamaz

# Anlık görüntü okuma
s = feed.snapshot("GARAN")                  # tek sembol
hepsi = feed.snapshots()                    # tüm semboller
print(feed.stats())                         # {'symbols': …, 'updates': …}

# Watchlist'i canlı değiştirme (token korunur, yeniden giriş yapılmaz)
feed.set_watchlist({**watchlist, "equities": ["GARAN", "EREGL"]})

feed.stop()
```

Her tik geldiğinde çağrılacak bir fonksiyon verilebilir:

```python
def tik_geldi(sembol, kok, veri):
    print(sembol, veri.get("last"))

feed = MatriksFeed(watchlist=watchlist, on_update=tik_geldi)
```

Tikler diske yazılabilir. `FileTickStore` her tiki `data/<tarih>/<sembol>.jsonl` dosyasına ekler:

```python
feed = MatriksFeed(watchlist=watchlist, store=FileTickStore())
```

Kendi depo sınıfınızı da verebilirsiniz (Redis, veritabanı vb.). `update`, `snapshot`, `stats`,
`flush` ve `close` metodlarını sağlaması yeterlidir.

Watchlist bir sözlük ya da `watchlist.yaml` yolu olabilir. Topic eşlemesi:

| Watchlist anahtarı | Topic |
|---|---|
| `indices`, `fx`, `equities` | `mx/symbol/<KOD>@lvl2` |
| `futures`, `options` | `mx/derivative/<KOD>@lvl2` |

## Sembol Listesi

Tek çağrıda yaklaşık 16 bin enstrüman döner: hisse, vadeli, opsiyon, endeks, döviz, varant ve fon.

```python
from matriks_wrapper import fetch_symbols, normalize_symbols

ham = fetch_symbols()                       # satıcı alan adlarıyla
kayitlar = normalize_symbols(ham)           # okunur alan adlarıyla

# Opsiyon zinciri
opsiyonlar = [k for k in kayitlar
              if k["type"] == "option" and k["underlying"] == "GARAN" and not k["deleted"]]

# Endeks üyeliği
bist30 = [k["symbol"] for k in kayitlar
          if k["type"] == "equity" and "BIST30" in (k["indices"] or [])]
```

`normalize_symbols` çıktısındaki alanlar:

| Alan | Açıklama |
|---|---|
| `symbol` | Sembol kodu |
| `feed_code` | Abonelikte kullanılan kod (çoğunlukla `symbol` ile aynıdır) |
| `type` | `equity`, `future`, `option`, `index`, `fx`, `warrant`, `fund`, … |
| `description` | Enstrüman açıklaması |
| `underlying` | Dayanak varlık |
| `expiry` | Vade tarihi (`YYYY-MM-DD`) |
| `strike` | Kullanım fiyatı |
| `call_put` | `call` veya `put` |
| `contract_size` | Sözleşme büyüklüğü |
| `indices` | Endeks üyelikleri |
| `segment` | Pazar/segment kodu |
| `deleted` | Kotasyondan çıkmış enstrüman |

> **Not**: BIST'te endeks opsiyonlarının sözleşme büyüklüğü 10, hisse opsiyonlarının 100'dür.
> Düzeltilmiş kontratlarda farklı olabilir; değeri `contract_size` alanından okuyun.

## Geçmiş Veri

```python
from matriks_wrapper.history import get_bars, get_bars_csv

# Son N bar
bars = get_bars("GARAN", "1day", count=300)

# Tarih aralığı
bars = get_bars("GARAN", "1min", start="2026-06-01", end="2026-08-13")

# CSV çıktısı
csv = get_bars_csv("GARAN", "1day", count=300)
```

Her bar `time`, `date`, `open`, `high`, `low`, `close`, `volume`, `quantity` ve `vwap` alanlarını
içerir. Sonuç eskiden yeniye sıralıdır.

Desteklenen periyotlar: `1min`, `5min`, `1hour`, `1day`.

> **Not**: API istek başına yaklaşık 17 bin bar sınırına sahiptir. Geniş `start`/`end` aralıkları
> otomatik olarak parçalanır, sonuçlar birleştirilir ve tekrar eden barlar ayıklanır.

> **Not**: Bar verisi için ek lisans gerekmez, canlı feed ile aynı token kullanılır. Tick
> seviyesinde geçmiş veri (`tick/trade`, `tick/depth`) ayrı lisans ister ve `EACCES` döner.

## Komut Satırı

```bash
matriks-run        # giriş, dinleme, token tazeleme ve yeniden giriş; gözetimsiz çalışır
matriks-login      # yalnızca uzaktan giriş yapar, .secrets/ yazar
matriks-listen     # yalnızca dinler, yeniden giriş denemez
matriks-symbols    # sembol dağılımını yazdırır, kurulum doğrulaması için kullanılır
```

## Örnekler

```bash
python examples/01_login.py         # kimlik zinciri, token ömrü, lisanslar
python examples/02_semboller.py     # opsiyon zinciri, endeks üyeliği, watchlist üretimi
python examples/03_canli_veri.py    # abonelik, anlık görüntü okuma, canlı watchlist değişimi
python examples/04_gecmis_veri.py   # günlük ve dakikalık barlar, oynaklık hesabı
```

## Depo Yapısı

```
matriks-wrapper/
  pyproject.toml  LICENSE  .env.example  watchlist.yaml
  src/matriks_wrapper/
    __init__.py               # MatriksFeed ve dışa açılan diğer arayüzler
    supervisor.py             # giriş, dinleyici ve yeniden giriş orkestrasyonu
    auth.py  login.py         # C6 token üretimi ; Playwright ve Telegram ile giriş
    symbols.py  history.py    # sembol listesi ; OHLC barlar
    discovery.py  broker.py  mqtt_ws.py   # broker çözümü ; MQTT 3.1 over WebSocket
    decode.py  store.py       # protobuf çözümü ; bellek ve dosya tik depoları
    config.py  telegram_relay.py
    proto/matriks_pb2.py      # derlenmiş şema (proto/matriks.proto'dan üretilir)
  examples/   docs/   reference/   proto/matriks.proto
```

## Dokümanlar

Protokol dokümantasyonu `docs/` altındadır ve İngilizcedir.

| Doküman | İçerik |
|---|---|
| [01 Architecture](docs/01-architecture.md) | Veri katmanının yapısı |
| [02 Transport](docs/02-transport-mqtt.md) | MQTT over WebSocket, çerçeveleme, Origin |
| [03 Authentication](docs/03-auth.md) | Giriş zinciri, JWT, token ömrü |
| [04 Discovery](docs/04-discovery-brokers.md) | `disco-v2.json`, broker URL'leri, QoS |
| [05 Topics](docs/05-topics.md) | Topic isim uzayı, sembol sonekleri |
| [06 Protobuf](docs/06-protobuf-schema.md) | Mesaj tipleri, alan tabloları |
| [07 REST catalog](docs/07-rest-api-catalog.md) | 277 REST ucu |
| [08 Payload examples](docs/08-payload-examples.md) | Topic başına çözülmüş örnekler |
| [09 Listener plan](docs/09-architecture-plan.md) | Servis tasarımı |
| [10 Remote login](docs/10-remote-login.md) | Captcha ve OTP aktarımı |

`reference/` altında ham çıktılar bulunur: `disco-v2.full.json` (tam discovery dokümanı),
`payload-examples.json` (çözülmüş canlı örnekler), `rest-catalog.tsv` (277 REST ucu),
`mqtt-topics.tsv` (74 topic öneki ve broker eşlemesi). `extract_proto.py`, uygulama sürümü
değiştiğinde `proto/matriks.proto` dosyasını yeniden üretir.

## Teknik Notlar

| Konu | Değer |
|---|---|
| Gerçek zamanlı uç | `wss://rtank.radix.matriksdata.com:443/market` |
| Gecikmeli uç | `wss://dlank…` |
| Protokol | MQTT 3.1 (`MQIsdp`) over WebSocket, subprotocol `mqttv3.1` |
| Zorunlu başlık | `Origin` |
| MQTT kullanıcı adı | `JTW` (sabit; kimlik JWT içindedir) |
| Token | RS256 JWT, `iss:ZRTYAT`, 5 saat ömür |
| Token tazeleme | `GetUrl.aspx` üzerinden `MsgType=C6`, captcha sormaz |
| Veri formatı | Protocol Buffers, 19 mesaj tipi |

## Sorumluluk Reddi

Bu kütüphane aracılığıyla erişilen veriler ilgili kaynaklara aittir:

- **Borsa İstanbul**: Hisse, vadeli işlem ve opsiyon piyasası verileri
- **Matriks Bilgi Dağıtım Hizmetleri**: Veri dağıtımı, sembol referans verisi, geçmiş barlar
- **Ziraat Yatırım**: Web Trader erişimi ve lisanslama

Kütüphane kişisel kullanım ve eğitim amacıyla hazırlanmıştır. Ticari kullanım için Borsa İstanbul
ve veri sağlayıcısından izin almanız gerekebilir. Kütüphane hiçbir garanti vermez; kullanımından
doğacak sorumluluk kullanıcıya aittir.

## Lisans

MIT. Lisans yalnızca bu depodaki kodu kapsar; erişilen veriler yukarıdaki koşullara tabidir.

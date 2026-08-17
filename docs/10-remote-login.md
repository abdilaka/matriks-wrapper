# 10 – Remote login (Playwright + Telegram)

Fully-unattended login is impossible by design — the login has a **captcha** (image) and an **OTP
SMS**, both requiring a human. This flow keeps the human in the loop but **remote**: a real browser
runs on the server, and the captcha image + OTP prompt are relayed to you over Telegram. You reply
with the captcha text and the SMS code; the script captures the session and exits. After that, the
data service refreshes the JWT on its own (C6, no captcha) until the session dies.

Why Playwright (not an API re-implementation): the real client generates `HardwareID` and
`ParentRef` in its own JS; driving the real page avoids reproducing that. Why not an iframe: the
login page sends `X-Frame-Options: SAMEORIGIN` + CSP `frame-ancestors 'self'`, so it can't be
embedded on your domain — and cross-origin SOP would block reading the token anyway.

## Components
- `src/telegram_relay.py` — send text/photo, wait for your reply (long-poll `getUpdates`).
- `src/login_playwright.py` — drives `/tr/login`, taps `GetUrl.aspx` to capture session material +
  JWT, relays captcha/OTP via Telegram, writes `.secrets/session_store.json` + `.secrets/jwt.txt`.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
# Linux server also needs system libs:
playwright install-deps        # or: apt-get install the listed libs
```

`.env` (gitignored):
```
ZRY_CUSTOMER_NO=...      # müşteri numarası
ZRY_PASSWORD=...         # parola
TG_BOT_TOKEN=...         # @BotFather token
TG_CHAT_ID=...           # your chat id
HEADLESS=1               # 0 to watch locally while debugging
```

## Run

```bash
python src/login_playwright.py
```
Sequence:
1. Telegram: "🔐 Matriks login başlıyor…"
2. Telegram **photo**: the captcha → reply with the characters you see.
3. Script submits müşteri no + parola + captcha → OTP SMS is sent to your phone.
4. Telegram: "📲 SMS OTP kodunu yaz" → reply with the code.
5. On success: "✅ Login başarılı" and `.secrets/session_store.json` (+ `jwt.txt`) are written.

Then start the listener: `python -u src/service.py` (uses `auth.py` to keep the JWT fresh).

## Confirmed selectors (validated end-to-end 2026-06-14)
- müşteri no `#username`, parola `#password`, captcha `input[placeholder="Doğrulama kodu"]`,
  captcha image `img.captcha` (blob src → element screenshot).
- **OTP field**: on the OTP step the captcha input is replaced by a second, label-less password
  input (label "Sms Şifre") → `input[type="password"]:not(#password)`.
- **Giriş button has NO literal `type=submit` attribute** (its `.type` property defaults to submit
  inside the form), so CSS `button[type=submit]` does NOT match — click by text/role instead
  (`click_giris()` uses `get_by_role("button", name="Giriş")`). Same button on both steps.

A full run succeeded headless: steps `A, A, C3, C3`, session + token captured, `auth.py` then
minted a fresh JWT from the captured session.

## Retry (wrong captcha / OTP)
`remote_login()` retries each step up to 3×:
- **Captcha:** after Giriş, if the page doesn't advance to the OTP screen (still on captcha / error
  toast), it relays the reason, loads a **fresh captcha**, and asks again.
- **OTP:** if login doesn't complete after submitting, it asks for the OTP again (re-read the SMS).
- After 3 failed tries it aborts and notifies; the supervisor will retry on its next tick.

## Supervisor (unattended) — `src/supervisor.py`
Wraps the listener and owns the session lifecycle. Detection is **hybrid**:
- **Proactive periodic** (every 30 s): refresh the JWT before expiry. A *rejected* mint
  (`auth.SessionExpired`, i.e. C6 `State=false`) means the session is dead → re-login. Transient
  network errors are retried, not re-logged-in.
- **Reactive on broker health**: a mid-life session kill (e.g. a login elsewhere) leaves the cached
  JWT TTL-valid but drops the brokers. If no broker has been up for `BROKER_DOWN_GRACE` (90 s), force
  a fresh mint; if *that* is rejected → re-login.

Re-login coordination: stop the broker pool → `remote_login()` (captcha/OTP via Telegram) → mint a
fresh token → rebuild & restart the pool (respects the single-MQTT-session-per-account limit).
`RELOGIN_DEBOUNCE` (120 s) prevents stampedes. Run it instead of `service.py`:
`python -u src/supervisor.py`.

## Operational notes
- **Single active session per account** — running this login (or the listener's MQTT) invalidates
  any other live session (e.g. a logged-in browser). Run the login when nothing else is connected.
- **When to re-login:** only when the session dies (C6 starts failing). Day-to-day, `auth.py`
  refreshes the JWT silently. A supervisor can call `login_playwright` automatically when
  `auth.mint_token()` raises, turning this into hands-off-until-OTP recovery.
- **Security:** `.env`, `.secrets/` are gitignored. The bot token grants send/receive on your chat
  — keep it secret; rotate via @BotFather if leaked.

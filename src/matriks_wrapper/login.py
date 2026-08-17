"""Headless remote login via Playwright + Telegram human-in-the-loop.

Runs on a Linux server. Drives the real Matriks login page (so HardwareID/ParentRef are generated
correctly by their own JS), relays the captcha image and OTP prompt to the user over Telegram, and
captures the session material + JWT into .secrets/. The live data service then refreshes the JWT via
auth.py (C6) without any further human step until the session dies.

Prereqs:
    pip install playwright requests
    playwright install chromium          # (on Linux also: playwright install-deps)
Env (.env):
    ZRY_CUSTOMER_NO, ZRY_PASSWORD, TG_BOT_TOKEN, TG_CHAT_ID
    HEADLESS=1 (default; set 0 to watch locally)

Run:  python src/login_playwright.py
"""
import asyncio
import json
import os
import sys
import time


from . import config
from .telegram_relay import TelegramRelay

LOGIN_URL = config.ORIGIN + "/tr/login"
SECRETS = os.path.join(config.ROOT, ".secrets")
HEADLESS = os.environ.get("HEADLESS", "1") != "0"

# Tap GetUrl.aspx to capture session material (unredacted -> stays in .secrets) + the JWT.
TAP_JS = r"""
(function(){
  if (self.__cap) return;
  var C = self.__cap = { session:null, token:null, steps:[] };
  function parseForm(b){ var o={}; (b||'').split('&').forEach(function(kv){ var i=kv.indexOf('=');
    if(i>0) o[decodeURIComponent(kv.slice(0,i))]=decodeURIComponent(kv.slice(i+1).replace(/\+/g,' ')); }); return o; }
  function note(method, url, body, respText){
    if(!/GetUrl\.aspx/i.test(url)) return;
    var f = parseForm(body);
    if (f.MsgType) C.steps.push(f.MsgType);
    if (f.SessionKey && f.ParentRef && f.HardwareID){
      C.session = { CustomerNo:f.CustomerNo, Username:f.Username, Password:f.Password,
                    SessionKey:f.SessionKey, ParentRef:f.ParentRef, HardwareID:f.HardwareID,
                    SourceID:f.SourceID||'70', Version:f.Version||'5419', ExchangeID:f.ExchangeID||'4' };
    }
    if (respText){ try{ var j=JSON.parse(respText); if(j.MarketDataToken) C.token=j.MarketDataToken; }catch(e){} }
  }
  var of=self.fetch;
  self.fetch=function(input,init){ var url=(typeof input==='string')?input:(input&&input.url);
    var body=init&&init.body; var p=of.apply(this,arguments);
    return p.then(function(r){ try{ if(/GetUrl\.aspx/i.test(url||'')) r.clone().text().then(function(t){ note((init&&init.method)||'GET',url,typeof body==='string'?body:'',t);}).catch(function(){});}catch(e){} return r; }); };
  var oOpen=XMLHttpRequest.prototype.open, oSend=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){ this.__u=u; return oOpen.apply(this,arguments); };
  XMLHttpRequest.prototype.send=function(b){ var s=this;
    this.addEventListener('load',function(){ try{ note('POST',s.__u,typeof b==='string'?b:'',s.responseText);}catch(e){} });
    return oSend.apply(this,arguments); };
})();
"""

# OTP step: captcha field is replaced by a second, label-less password input ("Sms Şifre",
# helper: "Cep telefonunuza iletilen SMS şifresini giriniz."). Confirmed live 2026-06-14:
# the OTP field is the password input that is NOT #username/#password.
OTP_SELECTORS = [
    'input[type="password"]:not(#password)',
    'input[formcontrolname="otp"]',
    'input[formcontrolname="smsCode"]',
    'input[placeholder*="SMS" i]',
]


async def _ask(relay, fn, *a):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *a)


async def click_giris(page, timeout=20000):
    """Click the 'Giriş' button. The button has no literal type=submit attribute (its .type
    property defaults to submit inside the form), so match by text/role, not CSS [type=submit]."""
    for getter in (
        lambda: page.get_by_role("button", name="Giriş"),
        lambda: page.locator('button:has-text("Giriş")'),
        lambda: page.locator("button"),
    ):
        loc = getter().first
        try:
            await loc.wait_for(state="visible", timeout=5000)
            await loc.click(timeout=timeout)
            return True
        except Exception:
            continue
    raise RuntimeError("Giriş button not clickable")


MAX_CAPTCHA_TRIES = 3
MAX_OTP_TRIES = 3


async def screen_state(page):
    """'loggedin' | 'otp' | 'captcha' | 'unknown' from current DOM/URL."""
    if "/tr/main" in page.url:
        return "loggedin"
    for sel in OTP_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                return "otp"
        except Exception:
            pass
    try:
        cap = page.locator('input[placeholder="Doğrulama kodu"]').first
        if await cap.count() and await cap.is_visible():
            return "captcha"
    except Exception:
        pass
    return "unknown"


async def error_text(page):
    """Best-effort: any visible toast/error message (to relay why a step failed)."""
    try:
        return await page.evaluate(r"""() => {
            const sels = ['.k-notification','.toast','.mat-error','[class*="error" i]','[class*="hata" i]','[class*="uyari" i]'];
            for (const s of sels){ const e=document.querySelector(s);
                if (e && e.offsetParent!==null && (e.innerText||'').trim()) return e.innerText.trim().slice(0,140); }
            const t=(document.body.innerText||''); const lines=t.split('\n');
            for (const l of lines){ if(/(hatal[ıi]|geçersiz|yanl[ıi]ş|do[ğg]rulanamad|s[üu]resi|tekrar deneyin)/i.test(l)) return l.trim().slice(0,140); }
            return '';
        }""")
    except Exception:
        return ''


async def wait_until(page, targets, timeout=14, want_token=False):
    """Poll screen_state until it hits one of `targets` (or token captured); return final state."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if want_token:
            cap = await page.evaluate("() => self.__cap || {}")
            if cap.get("token") and cap.get("session"):
                return "loggedin"
        st = await screen_state(page)
        if st in targets:
            return st
        await asyncio.sleep(1)
    return await screen_state(page)


async def reload_captcha(page):
    """Trigger a fresh captcha image (failed submit usually refreshes it; else click refresh)."""
    for sel in ['[class*="refresh" i]', '[class*="reload" i]', '[class*="yenile" i]',
                'img.captcha ~ button', 'img.captcha ~ *']:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(); break
        except Exception:
            pass
    await asyncio.sleep(1)


async def otp_locator(page):
    for sel in OTP_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass
    return None


async def capture_captcha(page, path, pad_x=150, pad_y=30):
    """Captcha img'ini çevresinden pad_x px (sağ/sol) + pad_y px (üst/alt) genişleterek yakala.
    Dar element screenshot yerine viewport'a clamp'li clip → Telegram'da daha okunaklı görünür.
    bounding_box alınamazsa düz element screenshot'a düşer. (device_scale_factor=2 ile 2x DPI.)"""
    loc = page.locator("img.captcha")
    try:
        box = await loc.bounding_box()
        vp = page.viewport_size or {"width": 1280, "height": 720}
        if box:
            x = max(0, box["x"] - pad_x)
            y = max(0, box["y"] - pad_y)
            w = min(vp["width"] - x, box["width"] + 2 * pad_x)
            h = min(vp["height"] - y, box["height"] + 2 * pad_y)
            await page.screenshot(path=path, clip={"x": x, "y": y, "width": w, "height": h})
            return
    except Exception:
        pass
    await loc.screenshot(path=path)


async def remote_login(relay=None, headless=None):
    """Drive the full login with captcha+OTP retry. Returns True on success (session_store + jwt
    written). Safe to call from the supervisor when the session dies."""
    cust = os.environ.get("ZRY_CUSTOMER_NO")
    pwd = os.environ.get("ZRY_PASSWORD")
    if not cust or not pwd:
        raise RuntimeError("set ZRY_CUSTOMER_NO and ZRY_PASSWORD (.env)")
    relay = relay or TelegramRelay()
    headless = HEADLESS if headless is None else headless
    os.makedirs(SECRETS, exist_ok=True, mode=0o700)
    try:
        os.chmod(SECRETS, 0o700)  # mevcut dizinde de daralt (makedirs mode'u sadece yeni oluşturmada geçerli)
    except OSError:
        pass
    captcha_png = os.path.join(SECRETS, "captcha.png")
    cap = {}

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            device_scale_factor=2,  # captcha çözünürlüğü için 2x DPI
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        await ctx.add_init_script(TAP_JS)
        page = await ctx.new_page()
        relay.send_text("🔐 Matriks login başlıyor…")
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=45000)
        await page.fill("#username", cust)
        await page.fill("#password", pwd)

        # --- captcha (with retry) ---
        state = "captcha"
        for attempt in range(1, MAX_CAPTCHA_TRIES + 1):
            await capture_captcha(page, captcha_png)
            relay.send_photo(captcha_png, f"Captcha kodunu yaz (deneme {attempt}/{MAX_CAPTCHA_TRIES}):")
            code = await _ask(relay, relay.wait_reply, 300)
            await page.fill('input[placeholder="Doğrulama kodu"]', code.strip())
            await click_giris(page)
            state = await wait_until(page, {"otp", "loggedin"}, timeout=12, want_token=True)
            if state in ("otp", "loggedin"):
                break
            err = await error_text(page)
            if attempt < MAX_CAPTCHA_TRIES:
                relay.send_text(f"❌ Captcha kabul edilmedi{(' — ' + err) if err else ''}. "
                                f"Yeni captcha gönderiyorum.")
                await page.fill("#username", cust)  # re-fill in case form reset
                await page.fill("#password", pwd)
                await reload_captcha(page)
        if state not in ("otp", "loggedin"):
            relay.send_text("⛔ Captcha 3 denemede geçilemedi, login iptal.")
            await browser.close()
            return False

        # --- OTP (with retry) ---
        if state == "otp":
            for attempt in range(1, MAX_OTP_TRIES + 1):
                prompt = ("📲 SMS ile gelen OTP kodunu yaz:" if attempt == 1
                          else f"❌ OTP kabul edilmedi. Tekrar yaz (deneme {attempt}/{MAX_OTP_TRIES}):")
                otp = await _ask(relay, relay.ask, prompt, 300)
                loc = await otp_locator(page)
                if loc is None:
                    relay.send_text("OTP alanı bulunamadı; login iptal.")
                    await browser.close()
                    return False
                await loc.fill(otp.strip())
                try:
                    await click_giris(page)
                except Exception:
                    await loc.press("Enter")
                state = await wait_until(page, {"loggedin"}, timeout=15, want_token=True)
                if state == "loggedin":
                    break
                if attempt >= MAX_OTP_TRIES:
                    err = await error_text(page)
                    relay.send_text(f"⛔ OTP 3 denemede geçilemedi{(' — ' + err) if err else ''}, login iptal.")
                    await browser.close()
                    return False

        cap = await page.evaluate("() => self.__cap || {}")
        await browser.close()

    if not cap.get("session"):
        relay.send_text("❌ Login tamamlandı görünmüyor (session yakalanamadı).")
        print("FAIL: no session captured; steps =", cap.get("steps"))
        return False
    # Kimlik bilgileri: yalnızca sahibi okuyabilsin (0o600).
    def _write_private(path, text):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)

    _write_private(os.path.join(SECRETS, "session_store.json"),
                   json.dumps(cap["session"], ensure_ascii=False, indent=2))
    if cap.get("token"):
        _write_private(os.path.join(SECRETS, "jwt.txt"), cap["token"])
    relay.send_text("✅ Login başarılı, session kaydedildi.")
    print("OK: session_store.json written; token captured:", bool(cap.get("token")),
          "; steps:", cap.get("steps"))
    return True


def main():
    """Console entry point: `matriks-login` / `python -m matriks_wrapper.login`."""
    ok = asyncio.run(remote_login())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

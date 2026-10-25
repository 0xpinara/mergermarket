"""Record a real, continuous MergeProof walkthrough in Chromium, driven by Playwright and paced by the narration.

    python scripts/demo_video/record.py      # needs demo-build/narration (narrate.py) and a built frontend

Starts its own server on a fresh database, then drives the real UI: real clicks, real wheel scrolling, real typing
and real API calls behind them. Frames come from Chrome's DevTools screencast (every composited frame, live, as
JPEG at a native 2240x1260), and every scene start and zoom target is logged with wall-clock times, so
compose.py can place the narration and captions exactly. Nothing is mocked; the cursor is a DOM overlay that
follows the real mouse events, because headless Chromium draws no pointer."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "demo-build"
FRAMES = BUILD / "frames"
PORT = 8770
BASE = f"http://127.0.0.1:{PORT}"
LAYOUT_W = 1440    # the app is laid out exactly as on a 1440-px laptop screen...
VIEW = {"width": 2240, "height": 1260}   # ...and rendered natively at 2240x1260 (CSS zoom), so 1080p stays crisp
Z = VIEW["width"] / LAYOUT_W
GAP = 0.35          # breath between narration clips (s)

RENDER_JS = """
try { localStorage.setItem('mp.demoSeed', 'off'); } catch (e) {}
Object.defineProperty(window, 'devicePixelRatio', {get: () => %(z)f});
document.addEventListener('DOMContentLoaded', () => { document.body.style.zoom = '%(z)f'; });
""" % {"z": Z}

CURSOR_JS = (r"""
(() => {
  const Z = __Z__;
  const mk = () => {
    if (document.getElementById('__mp_cursor')) return;
    const c = document.createElement('div');
    c.id = '__mp_cursor';
    c.innerHTML = '<svg width="24" height="32" viewBox="0 0 24 32"><path d="M3 2.5 L3 25 L8.9 19.4 L13.3 29 L17.2 27.3 L12.9 18 L21 18 Z" fill="#0b1220" stroke="#fbf8f1" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '0', top: '0', zIndex: '2147483647', pointerEvents: 'none',
      transform: 'translate(' + (window.__mpX ?? 1120) + 'px,' + (window.__mpY ?? 660) + 'px) scale(' + Z + ')',
      transformOrigin: '0 0',
      filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.35))', willChange: 'transform'});
    const r = document.createElement('div');
    r.id = '__mp_ring';
    Object.assign(r.style, {position: 'fixed', left: '0', top: '0', width: 38 * Z + 'px', height: 38 * Z + 'px',
      marginLeft: -19 * Z + 'px', marginTop: -19 * Z + 'px', borderRadius: '50%', border: 2 * Z + 'px solid #a9dc45',
      background: 'rgba(198,243,107,.28)',
      zIndex: '2147483646', pointerEvents: 'none', opacity: '0'});
    document.documentElement.appendChild(r);
    document.documentElement.appendChild(c);
    addEventListener('mousemove', (e) => {
      window.__mpX = e.clientX; window.__mpY = e.clientY;
      c.style.transform = 'translate(' + (e.clientX - 3 * Z) + 'px,' + (e.clientY - 2 * Z) + 'px) scale(' + Z + ')';
      r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
    }, true);
    addEventListener('mousedown', () => r.animate([{opacity: 0.95, transform: 'scale(.35)'}, {opacity: 0, transform: 'scale(1.7)'}],
      {duration: 520, easing: 'cubic-bezier(.2,.7,.3,1)'}), true);
  };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', mk); else mk();
})();
""").replace("__Z__", str(Z))


def load_narration() -> list[dict]:
    d = BUILD / "narration"
    metas = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
    if not metas:
        raise SystemExit("Run narrate.py first.")
    return metas


def start_server(db: Path) -> subprocess.Popen:
    env = {**os.environ, "MERGEPROOF_DB": str(db), "MERGEPROOF_AI": "off"}
    env.pop("ELEVENLABS_API_KEY", None)  # the app never needs it
    p = subprocess.Popen([str(ROOT / "backend/.venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port",
                          str(PORT), "--log-level", "warning"], cwd=ROOT / "backend", env=env)
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1)
            return p
        except Exception:
            time.sleep(0.25)
    p.terminate()
    raise SystemExit("server did not start")


class Recorder:
    def __init__(self, page, cdp):
        self.page, self.cdp = page, cdp
        self.frames: list[tuple[float, str]] = []
        self.events: list[dict] = []
        self.n = 0
        self.pos = [720.0 * Z, 430.0 * Z]

    async def start(self):
        FRAMES.mkdir(parents=True, exist_ok=True)
        self.cdp.on("Page.screencastFrame", self._frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "maxWidth": VIEW["width"],
                                                     "maxHeight": VIEW["height"], "everyNthFrame": 1})

    def _frame(self, params):
        ts = params["metadata"].get("timestamp") or time.time()
        name = f"{self.n:06d}.jpg"
        (FRAMES / name).write_bytes(base64.b64decode(params["data"]))
        self.frames.append((ts, name))
        self.n += 1
        asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}))

    async def stop(self):
        await self.cdp.send("Page.stopScreencast")

    def mark(self, kind: str, **kw):
        self.events.append({"type": kind, "t": time.time(), **kw})

    # ------------------------------------------------------------------ human-like input
    async def move(self, x: float, y: float, dur: float = 0.55):
        """Eased, slightly arced pointer move that takes `dur` seconds of wall time, however long each input event
        takes to be composited (each one waits for a captured frame)."""
        x0, y0 = self.pos
        mx, my = (x0 + x) / 2 + (y - y0) * 0.06, (y0 + y) / 2 - (x - x0) * 0.06
        t0 = time.time()
        while True:
            p = min(1.0, (time.time() - t0) / dur) if dur > 0 else 1.0
            e = p * p * (3 - 2 * p)
            bx = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * mx + e ** 2 * x
            by = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * my + e ** 2 * y
            await self.page.mouse.move(bx, by)
            if p >= 1.0:
                break
            await asyncio.sleep(0.004)
        self.pos = [x, y]

    async def box(self, loc):
        await loc.wait_for(state="visible", timeout=15000)
        return await loc.bounding_box()

    async def hover(self, loc, dur: float = 0.55, dx: float = 0.5, dy: float = 0.5):
        b = await self.box(loc)
        await self.move(b["x"] + b["width"] * dx, b["y"] + b["height"] * dy, dur)
        return b

    async def ensure_visible(self, loc):
        """Smooth-scroll a target to the middle of the screen if it is under the sticky top bar or near the bottom
        (where captions sit). Works inside scrollable drawers too."""
        await loc.wait_for(state="visible", timeout=15000)
        b = await loc.bounding_box()
        if b is None or b["y"] < 110 * Z or b["y"] + b["height"] > VIEW["height"] - 150 * Z:
            await loc.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'smooth'})")
            await asyncio.sleep(0.55)

    async def click(self, loc, dur: float = 0.55, dx: float = 0.5, dy: float = 0.5, settle: float = 0.12):
        await self.ensure_visible(loc)
        await self.hover(loc, dur, dx, dy)
        await asyncio.sleep(settle)
        await self.page.mouse.down()
        await asyncio.sleep(0.07)
        await self.page.mouse.up()

    async def wheel(self, dy: float, dur: float = 0.8):
        dy *= Z
        done, t0 = 0.0, time.time()
        while True:
            p = min(1.0, (time.time() - t0) / dur)
            target = dy * (p * p * (3 - 2 * p))
            if abs(target - done) >= 1 or p >= 1.0:
                await self.page.mouse.wheel(0, target - done)
                done = target
            if p >= 1.0:
                break
            await asyncio.sleep(0.01)

    async def type(self, loc, text: str, delay: int = 28):
        await loc.press_sequentially(text, delay=delay)

    async def zoom(self, loc_or_box, hold: float, pad: float = 24, max_scale: float = 1.42):
        b = loc_or_box if isinstance(loc_or_box, dict) else await self.box(loc_or_box)
        self.mark("zoom", box=b, hold=hold, pad=pad, max_scale=max_scale)


async def to_top(page):
    await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    await asyncio.sleep(0.5)


class Scenes:
    def __init__(self, rec: Recorder, narr: list[dict]):
        self.rec, self.narr = rec, {m["id"]: m for m in narr}
        self.next_at = 0.0
        self.cur = None
        self.t_start = 0.0

    async def begin(self, sid: str):
        now = time.time()
        start = max(now, self.next_at)
        await asyncio.sleep(start - now)
        self.cur = self.narr[sid]
        self.t_start = time.time()
        self.rec.mark("scene", id=sid, audio=self.cur["audio"])
        print(f"  scene {sid:9s} at +{self.t_start - self.rec.t0:6.2f}s")

    async def cue(self, phrase: str, lead: float = 0.0):
        """Wait until the narration reaches `phrase` (minus `lead` seconds)."""
        words = [w["w"].strip(".,:;!?\"'").lower() for w in self.cur["words"]]
        target = phrase.lower().split()
        for i in range(len(words)):
            if words[i:i + len(target)] == target:
                t = self.t_start + self.cur["words"][i]["start"] - lead
                await asyncio.sleep(max(0.0, t - time.time()))
                return
        raise SystemExit(f"cue {phrase!r} not in scene {self.cur['id']}")

    async def end(self):
        self.next_at = max(time.time(), self.t_start + self.cur["speech_end"]) + GAP


async def api(page, method: str, path: str, body=None):
    return await page.evaluate("""async ([m, p, b]) => {
      const h = {'X-Workspace': localStorage.getItem('mp.workspace'), 'Content-Type': 'application/json'};
      const r = await fetch('/api' + p, {method: m, headers: h, body: b ? JSON.stringify(b) : undefined});
      return {status: r.status, body: await r.json().catch(() => null)};
    }""", [method, path, body])


async def run():
    narr = load_narration()
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    db = BUILD / "demo.db"
    for suf in ("", "-wal", "-shm"):
        Path(str(db) + suf).unlink(missing_ok=True)
    server = start_server(db)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--force-color-profile=srgb", "--disable-lcd-text"])
            ctx = await browser.new_context(viewport=VIEW, device_scale_factor=1, color_scheme="light")
            await ctx.add_init_script(RENDER_JS)
            await ctx.add_init_script(CURSOR_JS)
            page = await ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            await page.goto(BASE + "/")
            await page.evaluate("localStorage.setItem('mp.reviewer', 'Ana Reyes')")
            await page.reload()
            await page.wait_for_selector(".hero")
            await page.mouse.move(720 * Z, 430 * Z)
            await asyncio.sleep(2.0)  # let fonts settle and the ASCII scene start
            cdp = await ctx.new_cdp_session(page)
            rec = Recorder(page, cdp)
            await rec.start()
            rec.t0 = time.time()
            rec.mark("start")
            S = Scenes(rec, narr)
            await asyncio.sleep(0.6)

            # 1 · the problem
            await S.begin("intro")
            await rec.move(560 * Z, 300 * Z, 1.6)
            await S.cue("deposits")
            await rec.move(640 * Z, 360 * Z, 1.2)
            await S.cue("that's what")
            await rec.hover(page.get_by_role("button", name="Try sample acquisition").first, 0.9)
            await S.cue("mergeproof does", lead=-0.25)
            await rec.click(page.get_by_role("button", name="Try sample acquisition").first, 0.2, settle=0.05)
            await page.wait_for_url("**/workspace", timeout=20000)
            await page.wait_for_selector(".snap", timeout=20000)
            await S.end()

            # 2 · the deal
            await S.begin("deal")
            await S.cue("cedar is acquiring", lead=0.5)
            await rec.click(page.locator(".deal-switch-btn"), 0.5)
            await page.wait_for_selector(".deal-switch .menu")
            await rec.hover(page.locator(".deal-switch .menu .item.current"), 0.4)
            await asyncio.sleep(0.35)
            await rec.click(page.locator(".deal-switch .menu .item.current"), 0.15, settle=0.05)
            await S.cue("a hundred and eighty-seven", lead=0.1)
            await rec.move(900 * Z, 520 * Z, 0.4)
            await rec.wheel(480, 1.1)
            await S.end()

            # 3 · profile + propose a mapping
            await S.begin("map")
            await rec.click(page.locator(".nav a", has_text="Integrate"), 0.7)
            await page.wait_for_selector(".rail")
            await rec.click(page.get_by_role("button", name="01 Profile"), 0.6)
            await page.wait_for_selector(".refmap")
            await S.cue("which columns", lead=0.4)
            units = page.locator(".rm-file").filter(has=page.locator(".rm-head b", has_text="bayview_units.csv"))
            await rec.ensure_visible(units)
            await rec.hover(units.locator(".rm-key.lonely"), 0.7, dx=0.25)
            await rec.zoom(units, hold=2.6)
            await S.cue("then it proposes", lead=0.5)
            await to_top(page)
            await rec.click(page.get_by_role("button", name="02 Map"), 0.45)
            await rec.click(page.get_by_role("button", name="Propose a mapping plan"), 0.45)
            await page.wait_for_selector(".plan-table", timeout=20000)
            await S.cue("template", lead=0.1)
            await rec.move(980 * Z, 560 * Z, 0.4)
            await rec.wheel(560, 1.4)
            await S.end()

            # 4 · the contested key, and the tempting mistake
            await S.begin("contested")
            ev = page.get_by_role("button", name="Evidence for units.unit_code")
            await rec.click(ev, 0.6)
            await page.wait_for_selector(".drawer .cand2")
            await asyncio.sleep(0.4)
            await rec.move(1100 * Z, 520 * Z, 0.4)
            await rec.wheel(300, 0.9)
            await S.cue("cedar's key", lead=0.2)
            await rec.zoom(page.locator(".drawer .cands2"), hold=4.2, max_scale=1.35)
            await rec.hover(page.locator(".drawer .cand2").nth(1), 0.7)
            await S.cue("nothing references", lead=0.1)
            note = page.locator(".drawer textarea")
            await rec.click(note, 0.4)
            await rec.type(note, "Template calls the key unit_code.", 16)
            pick = page.locator(".drawer .cand2", has=page.locator(".ch .b", has_text="unit_code")) \
                .get_by_role("button", name="Use this")
            await rec.hover(pick, 0.45)
            await S.cue("rushed choice", lead=0.1)
            await rec.click(pick, 0.1, settle=0.05)
            await page.wait_for_selector(".drawer .cand2.current:has(.ch .b:text-is('unit_code'))")
            # Off-camera bookkeeping a reviewer would do anyway: approve the remaining columns (real API calls,
            # logged in the plan's decision log). The UI picks them up with its next refresh.
            did = page.url.split("/deals/")[1].split("/")[0]
            r = await api(page, "POST", f"/deals/{did}/integrate/plan/bulk-approve", {"reviewer": "Ana Reyes"})
            for it in r["body"]["plan"]["items"]:
                if it["status"] not in ("approved", "ignored"):
                    await api(page, "POST", f"/deals/{did}/integrate/plan/items/{it['id']}",
                              {"action": "approve", "reviewer": "Ana Reyes",
                               "note": "Checked against the reference evidence." if it["needs_note"] else None})
            # Let the open page notice (React Query refetches on visibility change), so the stepper is truthful.
            await page.evaluate("window.dispatchEvent(new Event('visibilitychange'))")
            await asyncio.sleep(0.3)
            await page.keyboard.press("Escape")
            await S.end()

            # 5 · dry run: the mistake, priced
            await S.begin("dryrun")
            await to_top(page)
            await rec.click(page.get_by_role("button", name="04 Simulate"), 0.6)
            await page.wait_for_selector(".sim-head")
            await S.cue("it simulates", lead=0.3)
            await rec.click(page.get_by_role("button", name="Run dry run"), 0.6)
            await page.wait_for_selector(".blast", timeout=20000)
            await asyncio.sleep(0.3)
            await rec.move(980 * Z, 470 * Z, 0.5)
            await rec.wheel(330, 1.0)
            await S.cue("four hundred and eighteen", lead=0.35)
            region = await page.evaluate("""() => { const a = document.querySelector('.sim-kpis').getBoundingClientRect();
              const b = document.querySelector('.blast').getBoundingClientRect();
              return {x: Math.min(a.x, b.x), y: a.y, width: Math.max(a.right, b.right) - Math.min(a.x, b.x),
                      height: b.bottom - a.y}; }""")
            await rec.zoom(region, hold=5.2, max_scale=1.3)
            await rec.hover(page.locator(".sim-kpis .kpi").nth(1), 0.8)
            await S.cue("three hundred eighty-seven", lead=0.4)
            await rec.hover(page.locator(".blast .impact").first, 0.9)
            await S.end()

            # 6 · fix it
            await S.begin("fix")
            await to_top(page)
            await rec.click(page.get_by_role("button", name="02 Map"), 0.4)
            ev = page.get_by_role("button", name="Evidence for units.unit_code")
            await rec.click(ev, 0.4)
            await page.wait_for_selector(".drawer .cand2")
            note = page.locator(".drawer textarea")
            await rec.click(note, 0.3)
            await rec.type(note, "Leases reference unit_id.", 9)
            pick = page.locator(".drawer .cand2", has=page.locator(".ch .b", has_text="unit_id")) \
                .get_by_role("button", name="Use this")
            await rec.click(pick, 0.35)
            await page.wait_for_selector(".drawer .cand2.current:has(.ch .b:text-is('unit_id'))")
            await page.keyboard.press("Escape")
            await to_top(page)
            await rec.click(page.get_by_role("button", name="04 Simulate"), 0.4)
            await S.end()

            await S.begin("rerun")
            await S.cue("run it again", lead=0.35)
            await rec.click(page.get_by_role("button", name="Run dry run again"), 0.4)
            await page.wait_for_selector(".diffline", timeout=20000)
            await page.wait_for_selector("text=every lease, tenant, deposit and work order")
            await S.end()

            await S.begin("fixed")
            await rec.zoom(page.locator(".sim-kpis"), hold=1.6, max_scale=1.25)
            await rec.hover(page.locator(".sim-kpis .kpi").nth(1), 0.6)
            await S.cue("every dollar", lead=0.4)
            await rec.move(980 * Z, 500 * Z, 0.4)
            await rec.wheel(470, 1.0)
            await rec.zoom(page.locator(".card", has_text="Tie-outs"), hold=2.4, max_scale=1.3)
            await S.end()

            # 7 · sign-off
            await S.begin("signoff")
            await to_top(page)
            await rec.click(page.get_by_role("button", name="05 Export"), 0.5)
            await page.wait_for_selector(".gates")
            ack = page.locator(".ack input")
            await rec.click(ack, 0.6)
            await rec.click(page.get_by_role("button", name="Sign off as Ana Reyes"), 0.5)
            await page.wait_for_selector(".stamp-ink")
            await asyncio.sleep(0.4)
            await S.end()

            # 8 · verify the actual load
            await S.begin("verify")
            await to_top(page)
            await rec.click(page.get_by_role("button", name="06 Verify"), 0.5)
            await rec.click(page.get_by_role("button", name="Run post-import verification"), 0.5)
            await page.wait_for_selector(".tie3", timeout=30000)
            await S.cue("and the load", lead=0.5)
            await rec.zoom(page.locator(".kpis.three"), hold=3.0, max_scale=1.35)
            await rec.hover(page.locator(".kpis.three .kpi").nth(1), 0.7)
            await S.cue("no plan", lead=0.2)
            await rec.move(900 * Z, 520 * Z, 0.4)
            await rec.wheel(640, 1.2)
            await S.end()

            # 9 · a real missing lease, with its source row
            await S.begin("lease")
            lease = page.locator(".card", has_text="Import-time defects").get_by_role("link", name="Missing lease")
            await rec.click(lease, 0.6)
            await page.wait_for_selector(".drawer .compare", timeout=20000)
            await asyncio.sleep(0.5)
            await S.cue("twenty-two fifty", lead=0.6)
            await rec.zoom(page.locator(".drawer"), hold=4.2, max_scale=1.3)
            await rec.move(1100 * Z, 560 * Z, 0.4)
            await rec.wheel(260, 1.0)
            await S.end()

            # 10 · the money: a rent cut and a missing deposit
            await S.begin("money")
            await page.keyboard.press("Escape")
            theo = page.locator("tr", has_text="Theo Alvarez").filter(has_text="Rent changed (lower)") \
                .locator(".row-title")
            await rec.click(theo, 0.7)
            await page.wait_for_selector(".drawer .compare")
            await S.cue("four hundred and fifty", lead=0.5)
            await rec.zoom(page.locator(".drawer .compare"), hold=2.8, max_scale=1.4)
            await S.cue("and deposits", lead=0.3)
            await page.keyboard.press("Escape")
            mina = page.locator("tr", has_text="Mina Okafor").filter(has_text="Deposit balance missing") \
                .locator(".row-title")
            await rec.click(mina, 0.7)
            await page.wait_for_selector(".drawer .compare")
            await S.cue("totals hide", lead=0.4)
            await rec.zoom(page.locator(".drawer"), hold=2.6, max_scale=1.3)
            await S.end()

            # 11 · close on the real unit map
            await S.begin("close")
            await page.keyboard.press("Escape")
            await rec.click(page.locator(".nav a", has_text="Command Center"), 0.6)
            await page.evaluate("window.scrollTo({top: 0})")
            await page.wait_for_selector(".hero-city .win.s-critical", timeout=20000)
            await rec.move(1250 * Z, 300 * Z, 1.2)
            await asyncio.sleep(max(0.0, S.t_start + S.cur["speech_end"] + 2.2 - time.time()))
            rec.mark("end")
            await rec.stop()
            await asyncio.sleep(0.3)
            (BUILD / "timeline.json").write_text(json.dumps(
                {"t0": rec.t0, "view": VIEW, "scale": 1, "frames": rec.frames, "events": rec.events,
                 "errors": errors}, indent=0))
            print(f"frames {len(rec.frames)}  duration {rec.events[-1]['t'] - rec.t0:.1f}s  errors {errors}")
            await browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

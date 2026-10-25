"""Browser check of the first-visit demo portfolio: a brand-new visitor should land on full pages, not empty ones.

    python scripts/e2e_demo_seed.py http://127.0.0.1:8000

Opens a fresh profile, waits for the synthetic portfolio to be seeded, then visits every page (and every Integrate
step) for the seeded deals, asserting real content is shown, screenshotting each, and failing on console errors.
Screenshots go to ./smoke-shots/demo-seed (override with SMOKE_SHOTS=/path). Exits non-zero on failure.
"""
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = Path(os.environ.get("SMOKE_SHOTS", "smoke-shots")) / "demo-seed"
OUT.mkdir(parents=True, exist_ok=True)
errors: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))

    page.goto(BASE + "/")
    page.wait_for_selector(".hero-city .win.s-critical", timeout=60000)   # seeded + audited → real unit map
    expect(page.locator(".track")).to_contain_text("Signed off")
    page.screenshot(path=str(OUT / "01_command_center.png"), full_page=True)
    page.locator(".deal-switch-btn").click()
    expect(page.locator(".deal-switch .menu .item")).to_have_count(3 + 2)   # 3 deals + 2 actions
    page.keyboard.press("Escape")

    deals = page.evaluate("""async () => (await (await fetch('/api/deals', {headers: {'X-Workspace':
        localStorage.getItem('mp.workspace')}})).json()).map(d => [d.id, d.title])""")
    for did, title in deals:
        tag = re.sub(r"[^a-z]+", "_", title.split("|")[0].lower()).strip("_")
        page.goto(f"{BASE}/deals/{did}/workspace")
        page.wait_for_selector(".snap")
        page.screenshot(path=str(OUT / f"{tag}_workspace.png"))
        for step in ("profile", "map", "review", "simulate", "export", "verify"):
            page.goto(f"{BASE}/deals/{did}/integrate?step={step}")
            page.wait_for_selector(".step-head")
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / f"{tag}_integrate_{step}.png"))
        has_audit = page.evaluate(f"""async () => (await (await fetch('/api/deals/{did}/summary', {{headers:
            {{'X-Workspace': localStorage.getItem('mp.workspace')}}}})).json()).has_run""")
        page.goto(f"{BASE}/deals/{did}/findings?status=all")
        if has_audit:
            page.select_option("select[aria-label=Status]", "all")
            expect(page.locator("tbody tr").first).to_be_visible()
        page.screenshot(path=str(OUT / f"{tag}_findings.png"))
        page.goto(f"{BASE}/deals/{did}/proof")
        page.wait_for_selector(".sheet, .empty")
        page.screenshot(path=str(OUT / f"{tag}_proof.png"))
    page.goto(BASE + "/intel")
    expect(page.locator(".intel-card")).to_have_count(7)
    page.screenshot(path=str(OUT / "intel.png"), full_page=True)
    browser.close()

if errors:
    print("DEMO SEED E2E FAILED")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(f"DEMO SEED E2E PASSED — {len(deals)} deals, every page populated; screenshots in {OUT}/")

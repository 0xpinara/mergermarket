"""Browser end-to-end test of the Integrate journey (Plan → Dry run → Verify) on the synthetic sample.

    pip install playwright && python -m playwright install chromium
    python scripts/e2e_integrate.py http://127.0.0.1:8000

Walks the demo script: propose a plan, make the tempting unit-key mistake, watch the dry run reject 418 rows, fix
it, sign off, download the package, verify the actual load, review the import-time defects and issue a release
receipt. Also checks every Integrate step at phone width for horizontal overflow and fails on console errors.
Screenshots go to ./smoke-shots/integrate (override with SMOKE_SHOTS=/path). Exits non-zero on failure.
"""
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = Path(os.environ.get("SMOKE_SHOTS", "smoke-shots")) / "integrate"
OUT.mkdir(parents=True, exist_ok=True)
errors: list[str] = []


def shot(page, name, full=True):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)


def step(page, n: str):
    page.get_by_role("button", name=re.compile(f"^{n}")).click()


def drawer_choose(page, item: str, source: str, note: str):
    page.get_by_role("button", name=f"Evidence for {item}").click()
    page.wait_for_selector(".drawer .cand2")
    page.locator(".drawer textarea").fill(note)
    page.locator(".drawer .cand2", has=page.locator(".ch .b", has_text=re.compile(f"^{source}$"))) \
        .get_by_role("button", name="Use this").click()
    page.wait_for_selector(f".drawer .cand2.current:has(.ch .b:text-is('{source}'))")
    page.keyboard.press("Escape")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('mp.demoSeed', 'off'); } catch (e) {}")  # these tests start from an empty workspace
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))

    page.goto(BASE + "/")
    page.get_by_role("button", name="Try sample acquisition").first.click()
    page.wait_for_url("**/workspace")
    did = re.search(r"/deals/([^/]+)/", page.url).group(1)
    page.get_by_role("link", name=re.compile("Integrate")).first.click()
    page.wait_for_selector(".rail")

    step(page, "01")
    page.wait_for_selector(".refmap")
    expect(page.locator(".rm-key.lonely", has_text="unit_code")).to_be_visible()
    shot(page, "01_profile")

    step(page, "02")
    page.get_by_role("button", name="Propose a mapping plan").click()
    page.wait_for_selector(".plan-table")
    expect(page.locator(".irow.s-needs_review")).to_have_count(2)
    shot(page, "02_map")

    step(page, "03")
    page.get_by_role("button", name=re.compile(r"^Approve \d+ as")).click()
    expect(page.locator(".review-top .btn.primary")).to_contain_text("Approve 0")
    # The tempting mistake: the template calls the key unit_code, and so does Bayview's door-code column.
    step(page, "02")
    drawer_choose(page, "units.unit_code", "unit_code", "Template calls the key unit_code.")
    expect(page.locator(".irow.warn")).to_have_count(3)  # the key and both unit references that depend on it
    step(page, "03")
    for _ in range(12):
        items = page.locator(".queue li")
        if items.count() == 0:
            break
        first = items.first
        quick = first.get_by_role("button", name="Approve", exact=True)
        if quick.count():
            quick.click()
        else:
            first.get_by_role("button", name="Decide →").click()
            page.wait_for_selector(".drawer .review-box")
            page.locator(".drawer textarea").fill("Checked against the reference evidence.")
            page.locator(".drawer .review-box .btn.primary").click()
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    expect(page.locator(".queue li")).to_have_count(0)

    step(page, "04")
    page.get_by_role("button", name=re.compile("Run dry run")).click()
    page.wait_for_selector(".blast")
    expect(page.locator(".hazard")).to_contain_text("SIMULATED OUTPUT — NOT IMPORTED, NOT VERIFIED")
    expect(page.locator(".sim-kpis .kpi").nth(1)).to_contain_text("418")
    expect(page.locator(".blast").first).to_contain_text("$387,115/mo")
    expect(page.locator(".blast").first).to_contain_text("$319,975")
    shot(page, "03_dry_run_wrong_key")

    step(page, "02")
    drawer_choose(page, "units.unit_code", "unit_id", "Leases and work orders reference unit_id.")
    expect(page.locator(".irow.warn")).to_have_count(0)
    step(page, "04")
    page.get_by_role("button", name=re.compile("Run dry run")).click()
    page.wait_for_selector(".diffline")
    expect(page.locator(".sim-kpis .kpi").nth(1)).to_contain_text("0")
    expect(page.locator(".diffline")).to_contain_text("rejected 418 → 0")
    expect(page.locator(".ties .mark.fail")).to_have_count(0)
    shot(page, "04_dry_run_fixed")

    step(page, "05")
    page.wait_for_selector(".gates")
    expect(page.locator(".gates li.g-pass")).to_have_count(7)
    page.locator(".ack input").check()
    page.get_by_role("button", name=re.compile("Sign off as")).click()
    page.wait_for_selector(".stamp-ink")
    with page.expect_download() as dl:
        page.get_by_role("link", name=re.compile("Download import package")).click()
    zdata = Path(dl.value.path()).read_bytes()
    zf = zipfile.ZipFile(io.BytesIO(zdata))
    manifest = json.loads(zf.read("mergeproof_import/manifest.json"))
    assert manifest["status"] == "SIMULATED OUTPUT — NOT IMPORTED, NOT VERIFIED", manifest["status"]
    assert "mergeproof_import/import/cedar_import_leases.csv" in zf.namelist()
    shot(page, "05_export")

    step(page, "06")
    page.get_by_role("button", name="Run post-import verification").click()
    page.wait_for_selector(".tie3")
    defects = page.locator(".card", has_text="Import-time defects")
    for needle in ("−$2,250/mo", "−$450/mo", "$1,800 deposit"):
        expect(defects).to_contain_text(needle)
    shot(page, "06_verify")
    closed = page.evaluate("""async (did) => {
      const h = {'X-Workspace': localStorage.getItem('mp.workspace'), 'Content-Type': 'application/json'};
      const rows = await (await fetch(`/api/deals/${did}/findings`, {headers: h})).json(); let n = 0;
      for (const f of rows) if (f.status === 'open' && (['critical','high'].includes(f.severity) || f.rent_risk_cents || f.deposit_cents)) {
        await fetch(`/api/findings/${f.id}/review`, {method: 'POST', headers: h, body: JSON.stringify(
          {action: 'expected_exception', reviewer: 'Demo Reviewer', note: 'Corrected in Cedar after the load.'})}); n++; }
      return n; }""", did)
    page.reload()
    page.get_by_role("button", name="Issue release receipt").click()
    page.wait_for_selector("text=Stored receipt matches its hash")
    shot(page, "07_receipt")

    page.goto(BASE + "/")
    expect(page.locator(".track")).to_contain_text("Released")

    ws = page.evaluate("localStorage.getItem('mp.workspace')")
    m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
    m.add_init_script("try { localStorage.setItem('mp.demoSeed', 'off'); } catch (e) {}")  # these tests start from an empty workspace
    mp = m.new_page()
    mp.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
    mp.goto(BASE + "/")
    mp.evaluate(f"localStorage.setItem('mp.workspace', '{ws}')")
    for s in ("profile", "map", "review", "simulate", "export", "verify"):
        mp.goto(f"{BASE}/deals/{did}/integrate?step={s}")
        mp.wait_for_selector(".step-head")
        mp.wait_for_timeout(900)
        w = mp.evaluate("document.documentElement.scrollWidth")
        if w > 390:
            errors.append(f"mobile overflow on {s}: scrollWidth {w}")
        mp.screenshot(path=str(OUT / f"m_{s}.png"))
    browser.close()

if errors:
    print("INTEGRATE E2E FAILED")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(f"INTEGRATE E2E PASSED — {closed} findings reviewed, receipt issued; screenshots in {OUT}/")

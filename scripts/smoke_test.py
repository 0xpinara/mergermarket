"""Browser smoke test of the judge's happy path (desktop + mobile), with screenshots.

    pip install playwright && python -m playwright install chromium
    python scripts/smoke_test.py http://127.0.0.1:8000        # or your public URL

Each run uses a fresh browser profile, i.e. a new unauthenticated demo workspace.
Screenshots go to ./smoke-shots (override with SMOKE_SHOTS=/path).
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = Path(os.environ.get("SMOKE_SHOTS", "smoke-shots"))
OUT.mkdir(exist_ok=True)
errors = []


def shot(page, name, full=False):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, accept_downloads=True)
    page = ctx.new_page()
    # The deliberate invalid-upload check below returns HTTP 400; browsers log that as a console error.
    page.on("console", lambda m: m.type in ("error", "warning") and "status of 400" not in m.text
            and errors.append(f"console {m.type}: {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(BASE + "/")
    page.wait_for_selector("text=Did every lease")
    shot(page, "01_command_empty")

    page.get_by_role("button", name="Try sample acquisition").first.click()
    page.wait_for_url("**/workspace")
    page.wait_for_selector(".snap .fname:has-text(\"bayview_leases.csv\")")
    shot(page, "02_workspace_sample", full=True)

    page.get_by_role("button", name="Run continuity audit").click()
    page.wait_for_selector("text=audit complete", timeout=20000)
    shot(page, "03_workspace_ran")

    page.goto(BASE + "/")
    page.wait_for_selector("text=Continuity score")
    page.wait_for_timeout(1200)
    shot(page, "04_command_audited", full=True)

    page.get_by_role("link", name="Review findings").click()
    page.wait_for_selector("table.t")
    shot(page, "05_findings")

    page.get_by_role("button", name="Missing lease").first.click()
    page.wait_for_selector(".drawer .compare")
    page.wait_for_timeout(400)
    shot(page, "06_drawer_missing")
    page.locator(".drawer-body").evaluate("el => el.scrollTo(0, 700)")
    shot(page, "06b_drawer_missing_scrolled")

    # expected exception with reason
    page.locator("#rv-note").fill("Tenant gave notice and moved out 9/28 - lease terminated before cutover.")
    page.get_by_role("button", name="Mark expected exception").click()
    page.wait_for_selector(".drawer .stamp.expected")
    page.reload()
    page.wait_for_selector(".drawer .stamp.expected")
    expect(page.locator(".timeline")).to_contain_text("moved out 9/28")
    shot(page, "07_drawer_after_reload")
    page.keyboard.press("Escape")

    page.locator("select[aria-label=Status]").select_option("all")
    page.get_by_role("button", name="Rent changed (lower)").first.click()
    page.wait_for_selector(".drawer .crow.differs")
    page.wait_for_timeout(300)
    shot(page, "08_drawer_rent")
    page.keyboard.press("Escape")

    page.get_by_placeholder("Search tenant").fill("Mina")
    page.get_by_role("button", name="Deposit balance missing").first.click()
    page.wait_for_selector(".drawer .receipt")
    page.wait_for_timeout(300)
    shot(page, "09_drawer_deposit")
    page.keyboard.press("Escape")

    page.get_by_placeholder("Search tenant").fill("Alex")
    page.get_by_role("button", name="Needs review: possible lease match").first.click()
    page.wait_for_selector(".drawer .cand")
    page.locator(".drawer-body").evaluate("el => el.scrollTo(0, 500)")
    page.wait_for_timeout(300)
    shot(page, "10_drawer_ambiguous")
    page.keyboard.press("Escape")
    page.get_by_placeholder("Search tenant").fill("")

    page.goto(page.url.split("/findings")[0] + "/proof")
    page.wait_for_selector(".sheet")
    shot(page, "11_proof", full=True)
    with page.expect_download() as d:
        page.get_by_role("link", name="Download PDF").click()
    pdf = d.value.path()
    assert open(pdf, "rb").read(5) == b"%PDF-", "pdf download"
    with page.expect_download() as d:
        page.get_by_role("link", name="Findings CSV").click()
    csv_text = open(d.value.path(), encoding="utf-8-sig").read()
    assert "expected" in csv_text and "moved out 9/28" in csv_text, "csv includes review state"

    page.goto(BASE + "/intel")
    page.get_by_role("button", name="Add prospective integration").first.click()
    page.locator("#intel-company_name").fill("Harborline Rentals (fictional)")
    page.locator("#intel-source_link").fill("not a url")
    page.get_by_role("button", name="Save prospect").click()
    page.wait_for_selector("text=Use a full http(s):// link.")
    page.locator("#intel-source_link").fill("https://example.com/announcement")
    page.locator("#intel-notes").fill("Acquired a 120-unit portfolio in Port Wren. Ops lead open to a migration check.")
    page.get_by_role("button", name="Save prospect").click()
    page.wait_for_selector("text=Harborline Rentals (fictional)")
    shot(page, "12_intel")

    page.goto(BASE + "/about")
    page.wait_for_selector("text=verification layer")
    shot(page, "13_about")

    # invalid upload handled gracefully
    page.goto(BASE + "/")
    page.locator(".deal-switch-btn").click()
    page.get_by_role("menuitem", name="Start audit with my files").click()
    page.locator("#deal-title").fill("Upload test")
    page.get_by_role("button", name="Create workspace").click()
    page.wait_for_url("**/workspace")
    page.locator("input[type=checkbox]").check()
    page.locator("input[type=file]").first.set_input_files(files=[{"name": "bad.csv", "mimeType": "text/csv",
                                                                   "buffer": b"2250,U1,L1\n1900,U2,L2\n"}])
    page.wait_for_selector("text=looks like data")
    shot(page, "14_upload_error")

    # mobile
    m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    mp = m.new_page()
    mp.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
    mp.goto(BASE + "/")
    mp.get_by_role("button", name="Try sample acquisition").first.click()
    mp.wait_for_url("**/workspace")
    mp.get_by_role("button", name="Run continuity audit").click()
    mp.wait_for_selector("text=audit complete", timeout=20000)
    mp.goto(BASE + "/")
    mp.wait_for_selector("text=Continuity score")
    mp.wait_for_timeout(1200)
    shot(mp, "15_mobile_command", full=True)
    width = mp.evaluate("document.documentElement.scrollWidth")
    if width > 392:
        errors.append(f"mobile horizontal overflow: scrollWidth={width}")
    mp.locator(".attn-item").first.click()
    mp.wait_for_selector(".drawer .compare")
    mp.wait_for_timeout(300)
    shot(mp, "16_mobile_drawer")
    mp.keyboard.press("Escape")
    for path in ("/findings", "/proof", "/workspace"):
        did = mp.evaluate("localStorage.getItem('mp.deal')")
        mp.goto(f"{BASE}/deals/{did}{path}")
        mp.wait_for_timeout(900)
        w = mp.evaluate("document.documentElement.scrollWidth")
        if w > 392:
            errors.append(f"mobile overflow on {path}: {w}")
        shot(mp, f"17_mobile{path.replace('/', '_')}")
    mp.goto(BASE + "/")
    mp.locator(".menu-btn").click()
    mp.wait_for_timeout(300)
    shot(mp, "18_mobile_nav")
    browser.close()

print("SMOKE TEST FAILED:" if errors else f"SMOKE TEST PASSED — screenshots in {OUT}/")
for e in errors:
    print(" ", e)
sys.exit(1 if errors else 0)

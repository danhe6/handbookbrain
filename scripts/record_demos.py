"""Record the short feature clips shown on the Overview page, from the LIVE app (real graph, real answers).

    python -m hb --company gitlab-gtm serve          # in one terminal
    pip install playwright && python -m playwright install chromium
    python scripts/record_demos.py                   # writes web/media/{learn,ask,explore,impact,review,falkor}.webm
    python scripts/record_demos.py --only learn      # one clip

Each scenario first runs once without recording (fills the LLM cache, so the clip shows no long spinners),
then again while recording. A visible cursor is drawn so viewers can follow the clicks. Nothing is
approved or changed: the review clip opens a draft and stops there.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "media"
SIZE = {"width": 1120, "height": 700}   # a bit smaller than a laptop so text stays readable in the Overview preview

CURSOR = """
(() => {
  if (window.__hbc) return; window.__hbc = 1;
  const add = () => {
    const c = document.createElement('div');
    c.style.cssText = 'position:fixed;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;' +
      'background:rgba(201,100,66,.35);border:2px solid #C96442;z-index:99999;pointer-events:none;transition:transform .12s';
    document.body.appendChild(c);
    addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { c.style.transform = 'scale(.6)'; }, true);
    addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
  };
  if (document.body) add(); else addEventListener('DOMContentLoaded', add);
})();
"""


class Run:
    def __init__(self, page, base, fast=False, falkor="http://localhost:3000"):
        self.p, self.base, self.fast, self.falkor = page, base, fast, falkor
        self.t0, self.start = None, None

    def pause(self, s):
        if not self.fast:
            self.p.wait_for_timeout(int(s * 1000))

    def go(self, hash_):
        self.p.goto(self.base + "/" + hash_)
        self.p.wait_for_load_state("networkidle")
        if self.t0 is not None and self.start is None:
            self.start = round(time.time() - self.t0, 2)   # the clip loops from here (skips the blank page load)
        self.pause(1.2)

    def wait(self, sel, timeout=120):
        self.p.locator(sel).first.wait_for(state="visible", timeout=timeout * 1000)

    def click(self, sel, pause=0.9, nth=0):
        el = self.p.locator(sel).last if nth == -1 else self.p.locator(sel).nth(nth)
        el.wait_for(state="visible", timeout=60000)
        el.scroll_into_view_if_needed()
        b = el.bounding_box()
        x, y = b["x"] + min(b["width"] / 2, 60), b["y"] + b["height"] / 2
        self.p.mouse.move(x, y, steps=1 if self.fast else 22)
        self.pause(0.35)
        self.p.mouse.click(x, y)
        self.pause(pause)

    def scroll(self, dy, steps=12):
        for _ in range(steps):
            self.p.mouse.wheel(0, dy / steps)
            self.p.wait_for_timeout(1 if self.fast else 45)
        self.pause(0.6)

    def top(self, sel, offset=70):
        """Smooth-scroll so `sel` sits just under the header (keeps the interesting part in frame)."""
        self.p.evaluate("([s, o]) => { const e = document.querySelector(s); if (e) window.scrollTo({ top: e.getBoundingClientRect().top + scrollY - o, behavior: 'smooth' }); }", [sel, offset])
        self.pause(1.0)

    def has(self, sel):
        return self.p.locator(sel).count() > 0


def learn(r: Run):
    r.go("#/learn")
    r.pause(1.0)
    r.click("[data-path=overview]", 0.5)
    r.wait(".fcard .answer")
    r.pause(2.5)
    r.click("[data-path=decisions]", 0.5)
    r.wait(".ditem summary")
    r.pause(1.0)
    r.click(".ditem summary", 1.6)
    r.click(".ditem[open] .askbtn", 0.6)
    r.wait("#coach .chip")
    r.click("#coach .chip", 0.5)
    r.wait("#coach .msg.bot .answer")
    r.pause(3.5)
    if r.has("#coach .msg.bot .term"):
        r.click("#coach .msg.bot .term", 0.4)
        r.wait("#termpop p")
        r.pause(3.0)


def ask(r: Run):
    r.go("#/ask")
    r.wait(".qbtn")
    pick = ".qbtn[data-id]:has-text('25 to 31')"
    if not r.has(pick):
        pick = ".qbtn[data-id]:has-text('31')"
    if not r.has(pick):
        pick = ".qbtn[data-id]:has-text('threshold')" if r.has(".qbtn[data-id]:has-text('threshold')") else ".qbtn[data-id]"
    r.click(pick, 0.5)
    r.wait("#story .answer")
    r.pause(3.0)
    r.top("#g-story", 120)
    r.pause(2.5)
    r.top(".tl", 90)
    r.pause(1.5)
    if r.has("[data-impact]"):
        r.click("[data-impact]", 0.5)
        r.wait(".impact .card")
        r.pause(3.0)


def explore(r: Run):
    r.go("#/explore")
    r.wait("#g-explore svg")
    r.pause(1.0)
    r.top("#dbar-explore")
    r.pause(1.0)
    if r.has(".dchip"):
        r.click(".dchip", 2.2, nth=1)
        if r.has("g.n[aria-label^='Decision']"):
            r.click("g.n[aria-label^='Decision']", 2.5)
        if r.p.locator(".dchip").count() > 2:
            r.click(".dchip", 2.5, nth=2)
        r.click(".dchip", 1.5, nth=0)


def impact(r: Run):
    """An answer that was invalidated by a change and refreshed, and what that change affected."""
    r.go("#/ask")
    r.wait(".qbtn[data-id]")
    hit = ".ev.stale, .ev.earlier_answer"
    for sel in [".qbtn[data-id]:has-text('At how many points')", ".qbtn[data-id]:has-text('re-checked')",
                ".qbtn[data-id]:has-text('31')", ".qbtn[data-id]:has-text('trial')"]:
        for i in range(min(r.p.locator(sel).count(), 6)):
            r.click(sel, 0.3, nth=i)
            r.wait("#story .answer")
            r.p.wait_for_timeout(400)
            if r.has(hit):
                break
        if r.has(hit):
            break
    r.pause(1.5)
    r.top(".ev.change" if r.has(".ev.change") else ".tl", 120)
    r.pause(1.5)
    if r.has(hit):
        r.top(hit, 260)
        r.pause(2.5)
    if r.has(".ev.change [data-impact]"):
        r.click(".ev.change [data-impact]", 0.5, nth=-1)
        r.wait(".impact .card")
        r.pause(3.5)


def review(r: Run):
    r.go("#/review")
    r.pause(1.5)
    if r.has("[data-g=decided_not_documented]"):
        r.click("[data-g=decided_not_documented]", 1.2)
    if r.has("[data-draft]"):
        r.click("[data-draft]", 0.5)
        r.wait("#drawer-body .diff, #drawer-body .card")
        r.pause(3.0)
        r.p.mouse.move(900, 500)
        r.scroll(500)
        r.pause(2.5)
    elif r.has("[data-prop]"):
        r.click("[data-prop]", 3.0)


TRAIL = """MATCH p = (cv:Conversation)<-[:DECIDED_IN]-(d:Decision)-[:IMPLEMENTED_BY]->(c:Commit)-[:CHANGED]->(s:Section)
WHERE d.statement CONTAINS '31'
OPTIONAL MATCH b = (d)-[:DECIDED_BY]->(:Person)
OPTIONAL MATCH m = (:MergeRequest)-[:MERGED_AS]->(c)
OPTIONAL MATCH v = (s)-[:SUPERSEDES]->(:Section)
OPTIONAL MATCH a = (:Question)<-[:ANSWERS]-(:Answer)-[:CITES]->(s)
RETURN p, b, m, v, a"""


def falkor(r: Run):
    """The 25 -> 31 decision trail, queried live from FalkorDB on the Overview page, with the Cypher behind it."""
    r.go("#/overview")
    r.wait("#g-tabs")
    r.top("#g-tabs", 90)
    r.pause(1.2)
    r.click("[data-gt=trail]", 0.5)
    r.wait("#g-schema svg")
    r.pause(4.0)
    r.click("#g-cy summary", 0.6)
    r.top("#g-schema", 40)
    r.pause(1.0)
    r.p.mouse.wheel(0, 260)
    r.pause(4.0)


def falkor_browser(r: Run):
    """The same decision trail in FalkorDB Browser (needs the falkordb container's browser on --falkor)."""
    p = r.p
    p.goto(r.falkor)
    p.wait_for_load_state("networkidle")
    if "/login" in p.url:
        p.locator("button:has-text('Log in')").click()
        p.wait_for_url("**/graph", timeout=30000)
    p.locator("button:has-text('RUN')").first.wait_for(state="visible", timeout=30000)
    p.wait_for_timeout(800)
    for txt in ("Skip Tutorial",):
        if p.locator(f"button:has-text('{txt}')").count():
            p.locator(f"button:has-text('{txt}')").first.click()
    if r.t0 is not None and r.start is None:
        r.start = round(time.time() - r.t0, 2)
    sel = p.locator("button:has-text('gitlab-gtm'), [role=combobox]:has-text('gitlab-gtm')")
    if not sel.count():
        p.locator("button:has-text('Select Graph'), [role=combobox]").first.click()
        p.get_by_text("gitlab-gtm", exact=True).last.click()
    r.pause(1.0)
    ed = p.locator(".monaco-editor").first
    b = ed.bounding_box()
    p.mouse.move(b["x"] + 60, b["y"] + 12, steps=1 if r.fast else 20)
    p.mouse.click(b["x"] + 60, b["y"] + 12)
    lines = TRAIL.split("\n")
    for i in range(1, len(lines) + 1):    # line by line, so it reads like typing (Monaco would auto-close brackets)
        p.evaluate("(t) => window.monaco.editor.getModels()[0].setValue(t)", "\n".join(lines[:i]))
        r.pause(0.45)
    r.pause(0.8)
    close = p.locator("button:has-text('Close'), button[aria-label='Close']")
    if close.count():
        close.first.click()          # hide the Graph Info panel: more room for the graph
    r.click("button:has-text('RUN')", 0.5)
    r.pause(7.0)                     # let the force layout settle before fitting
    fit = p.locator("button:has-text('Center and fit'), [aria-label*='fit' i], [title*='fit' i]")
    if fit.count():
        fit.first.click()
    r.pause(5.0)


SCENES = {"learn": learn, "ask": ask, "explore": explore, "impact": impact, "review": review, "falkor": falkor,
          "falkor_browser": falkor_browser}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-warm", action="store_true")
    ap.add_argument("--falkor", default="http://localhost:3000", help="FalkorDB Browser (for the falkor clip)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / ".cache" / "clips"
    names = [n for n in SCENES if not a.only or n in a.only.split(",")]
    mf = OUT / "clips.json"
    meta = json.loads(mf.read_text()) if mf.exists() else {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name in names:
            if not a.no_warm:
                print(f"{name}: warming up (fills caches; can take a minute the first time)")
                ctx = b.new_context(viewport=SIZE)
                try:
                    SCENES[name](Run(ctx.new_page(), a.base, fast=True, falkor=a.falkor))
                except Exception as e:
                    print(f"  warm-up: {type(e).__name__}: {str(e)[:160]}")
                ctx.close()
            print(f"{name}: recording")
            shutil.rmtree(tmp, ignore_errors=True)
            ctx = b.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE)
            ctx.add_init_script(CURSOR)
            page = ctx.new_page()
            t = time.time()
            run = Run(page, a.base, falkor=a.falkor)
            run.t0 = t
            try:
                SCENES[name](run)
                page.wait_for_timeout(800)
            except Exception as e:
                print(f"  {type(e).__name__}: {str(e)[:200]}")
            vid = page.video.path() if page.video else None
            ctx.close()
            if vid and Path(vid).exists():
                dest = OUT / f"{name}.webm"
                shutil.move(vid, dest)
                meta[name] = {"start": run.start or 0}
                print(f"  -> {dest.relative_to(ROOT)} ({time.time() - t:.0f}s, {dest.stat().st_size // 1024} KB)")
        b.close()
    mf.write_text(json.dumps(meta, indent=1))
    print("Done. Reload the Overview page.")


if __name__ == "__main__":
    sys.exit(main())

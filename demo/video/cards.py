"""Render title cards and clip backdrops (1920x1080 PNG) in the app's visual style."""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright
import sys
CSS = """
*{box-sizing:border-box;margin:0}body{width:1920px;height:1080px;background:#FAF9F6;color:#1F1E1D;font-family:Inter,sans-serif;overflow:hidden}
.eb{font-size:22px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#9C4527}
.serif{font-family:'DejaVu Serif',Georgia,serif;font-weight:400;letter-spacing:-.01em}
.chip{border:1.5px solid #DAD6CD;background:#fff;border-radius:999px;padding:14px 26px;font-size:28px;color:#3D3B38;display:inline-block;margin:8px}
.logo{display:flex;gap:16px;align-items:center;font-size:34px;font-weight:600}
"""
LOGO = '<svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#1F1E1D" stroke-width="1.7" stroke-linecap="round"><circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.5 6h9M6.3 8.2l4.4 7.6M17.7 8.2l-4.4 7.6"/></svg>'
CARDS = {
"hook": f"""<div style="padding:150px 160px;display:flex;flex-direction:column;gap:44px">
  <div class="logo">{LOGO}HandbookBrain</div>
  <div class="serif" style="font-size:96px;line-height:1.05">One business rule.<br>Four places.<br><span style="color:#9C4527">Nobody knows what just broke.</span></div>
  <div>{''.join(f'<span class="chip">{x}</span>' for x in ['Meeting · Marketing Ops weekly','Handbook page','Old PDF on the drive','Dashboard'])}</div></div>""",
"oneplace": f"""<div style="padding:120px 140px;display:flex;flex-direction:column;gap:50px">
  <div class="eb">One versioned graph · FalkorDB</div>
  <div class="serif" style="font-size:78px;line-height:1.08">All of your company's knowledge<br>in one place.</div>
  <div style="display:grid;grid-template-columns:1fr 50px 1.15fr 50px 1.05fr;gap:20px;align-items:stretch;background:#fff;border:1.5px solid #E8E5DE;border-radius:28px;padding:22px">
   <div style="padding:26px"><div class="eb" style="color:#6B6966;font-size:18px;margin-bottom:16px">Where knowledge lives today</div>{''.join(f'<span class="chip" style="font-size:22px;padding:10px 18px;margin:5px">{x}</span>' for x in ['Meetings','Slack and Teams','Handbook','PDFs on the drive','CRM fields','Dashboards','Metrics'])}</div>
   <div style="display:grid;place-items:center;font-size:44px;color:#9A968F">→</div>
   <div style="background:#F7E6DE;border-radius:22px;padding:30px"><div class="eb" style="color:#6B6966;font-size:18px;margin-bottom:16px">GitLab GTM · Apr–Oct 2026</div>
     <div class="serif" style="font-size:46px;color:#9C4527;line-height:1.2">122 documents<br>21 decisions</div>
     <div style="font-size:24px;color:#6B6966;margin-top:16px">1,899 sections · 316 old versions<br>67 meetings and chats</div></div>
   <div style="display:grid;place-items:center;font-size:44px;color:#9A968F">→</div>
   <div style="padding:26px;font-size:30px;line-height:1.9"><div class="eb" style="color:#6B6966;font-size:18px;margin-bottom:6px">Who it serves</div><b>New hires</b> learn an area<br><b>Everyone</b> asks why<br><b>Owners</b> approve, not chase</div>
  </div></div>""",
"turn": f"""<div style="height:1080px;display:grid;grid-template-columns:1fr 1fr">
  <div style="background:#EDE9E1;display:flex;flex-direction:column;justify-content:center;padding:0 110px;gap:26px;color:#6B6966">
    <div class="eb" style="color:#6B6966">Before</div>
    <div class="serif" style="font-size:72px;line-height:1.1;color:#3D3B38">7 tools.<br>3 days.<br>Still guessing.</div></div>
  <div style="background:#FAF9F6;display:flex;flex-direction:column;justify-content:center;padding:0 110px;gap:26px">
    <div class="logo">{LOGO}HandbookBrain</div>
    <div class="serif" style="font-size:72px;line-height:1.1">1 question.<br>Seconds.<br><span style="color:#9C4527">The whole trail.</span></div></div></div>""",
"close": f"""<div style="height:1080px;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:40px;text-align:center">
  <div class="logo" style="font-size:52px">{LOGO.replace('44','64')}HandbookBrain</div>
  <div class="serif" style="font-size:76px;line-height:1.1">Everything your company knows, in one place.<br><span style="color:#9C4527">And still true tomorrow.</span></div>
  <div style="font-size:28px;color:#6B6966">Graph Hacks · Track 3 Company Brain · built on FalkorDB · github.com/danhe6/handbookbrain</div>
  <div style="font-size:20px;color:#9A968F;margin-top:30px">Voice-over is AI-generated (OpenAI TTS). Product footage recorded from the live app; opening scene is a simulated scenario.</div></div>""",
}
def backdrop(eyebrow, title):
    return f"""<div style="padding:34px 96px 0;display:flex;align-items:baseline;gap:22px"><span class="eb">{eyebrow}</span><span class="serif" style="font-size:40px">{title}</span></div>"""
BACK = {
 "learn": ("For new hires · Learn", "Learn an area by asking"),
 "ask": ("For everyone · Ask", "Ask why, get the trail back to the meeting"),
 "explore": ("Explore · decision triage", "One domain at a time, decisions ranked by impact"),
 "impact": ("Change impact", "Answers that go stale, and the blast radius of a change"),
 "review": ("For owners · Review", "The agent drafts, the owner approves"),
 "falkor": ("Under the hood · FalkorDB", "The real graph, one Cypher query away"),
}
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1920, "height": 1080})
    for k, html in CARDS.items():
        pg.set_content(f"<style>{CSS}</style>{html}"); pg.wait_for_timeout(200); pg.screenshot(path=f"card_{k}.png")
    for k, (e, t) in BACK.items():
        pg.set_content(f"<style>{CSS}</style>{backdrop(e, t)}"); pg.wait_for_timeout(100); pg.screenshot(path=f"back_{k}.png")
    b.close()
print("ok")

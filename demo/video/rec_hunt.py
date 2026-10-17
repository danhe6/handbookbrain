import sys, shutil, glob, os
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright
d = float(sys.argv[1]); out = sys.argv[2]
tmp = "hunt_tmp"; shutil.rmtree(tmp, ignore_errors=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=tmp, record_video_size={"width": 1920, "height": 1080})
    pg = ctx.new_page(); pg.goto(f"file://{os.path.abspath('hunt.html')}?d={d}")
    pg.wait_for_timeout(int(d * 1000) + 800)
    v = pg.video.path(); ctx.close(); b.close()
shutil.move(v, out); print(out)

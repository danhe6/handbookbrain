"""Assemble the demo video: title cards + live-app clips + the simulated-work opening, paced to the AI voice-over,
with burned-in captions. Re-run after any UI change:

    python scripts/record_demos.py                 # 1. re-record the app clips   (web/media/*.webm)
    python scripts/make_voice.py --voice marin     # 2. only if demo/narration.json changed
    python demo/video/cards.py                     # 3. title cards (edit numbers / text in cards.py)
    python demo/video/build.py . web/media demo/voice demo/handbookbrain-demo-raw.mp4
    ffmpeg -i demo/handbookbrain-demo-raw.mp4 -c:v copy -af loudnorm=I=-16:TP=-1.5:LRA=11 -c:a aac -b:a 192k \
           -movflags +faststart demo/handbookbrain-demo.mp4

Needs ffmpeg with libass (brew install ffmpeg) and playwright. Segment visuals come from narration.json
("scene:hunt" = hunt.html recorded live, "card:<name>" = cards.py, "clip:<name>" = web/media/<name>.webm).
"""
import json, re, subprocess, sys
from pathlib import Path
REPO = Path(sys.argv[1]); MEDIA = Path(sys.argv[2]); VOICE = Path(sys.argv[3]); OUT = Path(sys.argv[4])
HERE = Path(__file__).parent; W = HERE / "work"; W.mkdir(exist_ok=True)
segs = json.loads((REPO / "demo" / "narration.json").read_text())
clips = json.loads((MEDIA / "clips.json").read_text())
LEAD, TAIL = 0.35, 0.55
def dur(f): return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).decode().strip())
def run(cmd): subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
parts, t, cues = [], 0.0, []
for i, s in enumerate(segs):
    a = VOICE / f"{s['id']}.mp3"
    if a.exists():
        ad = dur(a)
    else:   # placeholder: silence at ~150 wpm
        ad = len(s["text"].split()) / 2.5
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{ad:.2f}", "-c:a", "libmp3lame", str(W / f"{s['id']}.mp3")]); a = W / f"{s['id']}.mp3"
    T = round(LEAD + ad + TAIL, 2)
    kind, name = s["visual"].split(":")
    v = W / f"{i:02d}.mp4"
    afilt = f"adelay={int(LEAD*1000)}:all=1,apad,atrim=0:{T},aformat=sample_rates=48000:channel_layouts=stereo"
    if kind == "scene":
        subprocess.run([sys.executable, str(HERE / "rec_hunt.py"), f"{T - 0.4:.2f}", str(W / "hunt.webm")], check=True, cwd=HERE, stdout=subprocess.DEVNULL)
        run(["ffmpeg", "-y", "-i", str(W / "hunt.webm"), "-i", str(a), "-filter_complex",
             f"[0:v]trim=start=0.5,setpts=PTS-STARTPTS,fps=30,scale=1920:1080,tpad=stop_mode=clone:stop_duration=3,trim=0:{T},fade=in:0:9,fade=out:st={T-0.3:.2f}:d=0.3[v];[1:a]{afilt}[a]",
             "-map", "[v]", "-map", "[a]", "-t", str(T), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", str(v)])
    elif kind == "card":
        frames = int(T * 30)
        vf = f"scale=2112:1188,zoompan=z='1+0.035*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps=30,fade=in:0:9,fade=out:st={T-0.3:.2f}:d=0.3"
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(HERE / f"card_{name}.png"), "-i", str(a), "-filter_complex", f"[0:v]{vf}[v];[1:a]{afilt}[a]",
             "-map", "[v]", "-map", "[a]", "-t", str(T), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", str(v)])
    else:
        src = MEDIA / f"{name}.webm"; st = clips.get(name, {}).get("start", 0.8) + 0.2
        L = dur(src) - st - 0.3
        T = round(max(T, L / 1.4), 2)            # never rush the UI past 1.4x; extra time = a natural beat after the line
        afilt = f"adelay={int(LEAD*1000)}:all=1,apad,atrim=0:{T},aformat=sample_rates=48000:channel_layouts=stereo"
        f = min(max(T / L, 0.7), 1.0 / 1.4 if T / L < 1 / 1.4 else T / L)
        clip = f"[1:v]trim=start={st:.2f},setpts={f:.3f}*(PTS-STARTPTS),scale=1376:860,tpad=stop_mode=clone:stop_duration={T:.2f},trim=0:{T}[c]"
        base = f"[0:v]scale=1920:1080,drawbox=x=270:y=97:w=1380:h=864:color=0xDAD6CD:t=2[b]"
        comp = f"{base};{clip};[b][c]overlay=272:99,fade=in:0:9,fade=out:st={T-0.3:.2f}:d=0.3[v];[2:a]{afilt}[a]"
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(HERE / f"back_{name}.png"), "-i", str(src), "-i", str(a), "-filter_complex", comp,
             "-map", "[v]", "-map", "[a]", "-t", str(T), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", str(v)])
    # captions: sentence chunks, timed by length across the voice
    chunks = []
    for sent in re.split(r"(?<=[.?!:])\s+", s["text"]):
        words = sent.split()
        while words:
            chunks.append(" ".join(words[:14])); words = words[14:]
    total = sum(len(c) for c in chunks); ct = t + LEAD
    for c in chunks:
        d = ad * len(c) / total; cues.append((ct, ct + d, c)); ct += d
    parts.append(v); t += T
(W / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
def ts(x): h = int(x // 3600); m = int(x % 3600 // 60); return f"{h}:{m:02d}:{x % 60:05.2f}"
ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Inter,40,&H001D1E1F,&H001D1E1F,&H00F6F9FA,&HB0F6F9FA,0,0,0,0,100,100,0,0,3,10,0,2,140,140,34,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "".join(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{c}\n" for a, b, c in cues)
(W / "caps.ass").write_text(ass)
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(W / "list.txt"), "-c", "copy", str(W / "joined.mp4")])
run(["ffmpeg", "-y", "-i", str(W / "joined.mp4"), "-vf", f"ass={W / 'caps.ass'}", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(OUT)])
print(f"{OUT}  {t:.1f}s")

"""AI voice-over for the demo video: one audio file per segment of demo/narration.json, via OpenAI TTS.

    source .venv/bin/activate
    python scripts/make_voice.py                # -> demo/voice/<id>.mp3
    python scripts/make_voice.py --voice nova   # also: marin (brighter), coral, ash, sage, verse, ballad

Uses OPENAI_API_KEY from .env. gpt-4o-mini-tts (steerable tone); falls back to tts-1-hd.
"""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
# labelled delivery spec (OpenAI speech-skill format): affect, tone, pacing, emotion, pauses, pronunciation, emphasis
TONE = """Voice Affect: Relaxed and warm, like a product lead showing a colleague something they built and like.
Tone: Conversational and natural. Not an announcer, not a presentation, not salesy.
Pacing: Moderate with natural variation; a little quicker through lists, slower on the key moment.
Emotion: Light, wry empathy in the problem story; quiet confidence when showing the product.
Pauses: Short natural pauses at commas and between sentences; a clear beat before "With HandbookBrain".
Pronunciation: "MQL" as M-Q-L; "CRM" as C-R-M; "FalkorDB" as "Falcor D-B"; "HandbookBrain" as "Handbook Brain".
Emphasis: Light stress on numbers, names and the last words of each sentence."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="cedar")   # cedar or marin: best quality per OpenAI docs
    ap.add_argument("--only", default="", help="comma-separated segment ids, e.g. 04b-falkor")
    a = ap.parse_args()
    load_dotenv(ROOT / ".env")
    client = OpenAI()
    out = ROOT / "demo" / "voice"
    out.mkdir(parents=True, exist_ok=True)
    for seg in json.loads((ROOT / "demo" / "narration.json").read_text()):
        if a.only and seg["id"] not in a.only.split(","):
            continue
        f = out / f"{seg['id']}.mp3"
        try:
            r = client.audio.speech.create(model="gpt-4o-mini-tts", voice=a.voice, input=seg["text"], instructions=TONE)
        except Exception as e:
            print(f"  gpt-4o-mini-tts unavailable ({type(e).__name__}); using tts-1-hd")
            r = client.audio.speech.create(model="tts-1-hd", voice=a.voice if a.voice in ("alloy", "echo", "fable", "onyx", "nova", "shimmer") else "nova", input=seg["text"])
        f.write_bytes(r.content)
        print(f"{f.relative_to(ROOT)}  ({f.stat().st_size // 1024} KB)")
    print("Done. Tell Claude; the video is assembled from demo/voice + web/media.")


if __name__ == "__main__":
    main()

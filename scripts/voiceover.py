#!/usr/bin/env python3
"""ElevenLabs voiceover with a local cache so the same line is never generated twice.

  voiceover.py --campaign <dir> --text "Wait for it." [--voice-id ID] [--out line.mp3]
  voiceover.py --campaign <dir> --ensure-voice            # adds the campaign voice to "My Voices" if needed
  voiceover.py --list-voices                              # premade + added voices on the account

Default voice: campaign.yaml audio.voice_id (Drew, q0IMILNRPxOgtBTS4taI). Model eleven_v4.
Cache: <campaign>/voiceover_cache/<sha1(voice|model|text)>.mp3. Never prints the key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from common import StepError, find_campaign, key, load_yaml, main_guard, out

API = "https://api.elevenlabs.io"
MODEL = "eleven_v4"
DREW_PUBLIC_OWNER = "675db5c1a341e2f820aefb00825680aa042d958a4a3aef3200735c799210dcb4"


def _req(path: str, method: str = "GET", body=None, raw: bool = False):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"xi-api-key": key("ELEVENLABS_API_KEY"), "Content-Type": "application/json", "User-Agent": "streamclips/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read() if raw else json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="ignore")[:300]
        if e.code == 401:
            raise StepError("ElevenLabs rejected the key (401). Ask the user to check ELEVENLABS_API_KEY.")
        if e.code == 402:
            raise StepError("ElevenLabs: this voice needs a paid plan (402). Pick a premade voice or upgrade.")
        raise StepError(f"ElevenLabs {e.code}: {msg}")


def my_voices() -> list[dict]:
    return _req("/v1/voices").get("voices", [])


def ensure_voice(voice_id: str, public_owner_id: str | None = None) -> dict:
    mine = {v["voice_id"]: v for v in my_voices()}
    if voice_id in mine:
        return {"voice_id": voice_id, "name": mine[voice_id]["name"], "added": False}
    owner = public_owner_id or DREW_PUBLIC_OWNER
    r = _req(f"/v1/voices/add/{owner}/{voice_id}", "POST", {"new_name": "streamclips voice"})
    return {"voice_id": r.get("voice_id", voice_id), "added": True}


def tts(text: str, voice_id: str, cache_dir: Path, stability: float = 0.45, similarity: float = 0.8) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(f"{voice_id}|{MODEL}|{text.strip()}".encode()).hexdigest()[:16]
    dst = cache_dir / f"{h}.mp3"
    if dst.exists() and dst.stat().st_size > 1000:
        return dst
    body = {"text": text.strip(), "model_id": MODEL, "voice_settings": {"stability": stability, "similarity_boost": similarity}}
    audio = _req(f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128", "POST", body, raw=True)
    dst.write_bytes(audio)
    (cache_dir / "index.jsonl").open("a", encoding="utf-8").write(json.dumps({"file": dst.name, "voice_id": voice_id, "text": text.strip()}) + "\n")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign")
    ap.add_argument("--text")
    ap.add_argument("--voice-id")
    ap.add_argument("--out")
    ap.add_argument("--ensure-voice", action="store_true")
    ap.add_argument("--list-voices", action="store_true")
    a = ap.parse_args()
    if a.list_voices:
        out([{"voice_id": v["voice_id"], "name": v["name"], "category": v.get("category")} for v in my_voices()])
        return
    campaign = find_campaign(a.campaign)
    cfg = load_yaml(campaign / "campaign.yaml")
    voice = a.voice_id or cfg.get("audio", {}).get("voice_id")
    if not voice:
        raise StepError("no voice id: set audio.voice_id in campaign.yaml or pass --voice-id")
    if a.ensure_voice:
        out({"ok": True, **ensure_voice(voice)})
        return
    if not a.text:
        raise StepError("--text is required")
    if len(a.text) > 400:
        raise StepError("voiceover text longer than 400 chars; keep lines short (hook/context only)")
    ensure_voice(voice)
    cache = campaign / "voiceover_cache"
    before = {x.name for x in cache.glob("*.mp3")} if cache.exists() else set()
    p = tts(a.text, voice, cache)
    if a.out:
        shutil.copy(p, a.out)
    out({"ok": True, "file": str(a.out or p), "cached": p.name in before, "chars": len(a.text)})


if __name__ == "__main__":
    main_guard(main)

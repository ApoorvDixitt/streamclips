import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np  # noqa: E402

import common  # noqa: E402
import fetch  # noqa: E402
import hook_check  # noqa: E402
import render  # noqa: E402
import signals  # noqa: E402
import transcribe  # noqa: E402


def test_parse_url():
    assert fetch.parse_url("https://www.twitch.tv/videos/2933758895") == ("twitch", "2933758895")
    assert fetch.parse_url("https://www.twitch.tv/kreekcraft") == ("twitch", "kreekcraft")
    assert fetch.parse_url("https://www.youtube.com/watch?v=jMCNK2H1jNQ") == ("youtube", "jMCNK2H1jNQ")
    assert fetch.parse_url("https://youtu.be/jMCNK2H1jNQ") == ("youtube", "jMCNK2H1jNQ")
    assert fetch.parse_url("https://kick.com/xqc") == ("kick", "xqc")


def test_parse_time_and_hms():
    assert common.parse_time("1:02:03") == 3723
    assert common.parse_time("90") == 90
    assert common.hms(3723.5) == "1:02:03.50"


def test_hook_check_rules():
    r = hook_check.check("Did you know Kreek robs stores", "kreek robbed the store", [])
    assert not r["ok"] and "filler opener" in r["problems"]
    r = hook_check.check("Kreek robs a store and leaves Jason behind", "I don't know if I want to rob this place. Jason run!", [])
    assert r["ok"] and r["transcript_support"] > 0
    r = hook_check.check("Aliens invade Vice City tonight", "I don't know if I want to rob this place", [])
    assert not r["ok"]  # no transcript support
    assert not hook_check.check("x " * 12, "", [])["ok"]  # too many words


def test_wrap_hook():
    lines, size = render.wrap_hook("Kreek robs a store and leaves Jason behind")
    assert len(lines) <= 2 and size <= 84
    try:
        render.wrap_hook("this hook is far too long to ever fit on two lines of large text on a phone screen")
        assert False
    except common.StepError:
        pass


def test_mask_word():
    assert render.mask_word("shit!") == "s***!"
    assert render.mask_word("Jason,") == "Jason,"


def test_chat_spam_filter():
    msgs = [{"t": 10 + i * 0.5, "user": f"u{i}", "text": "1"} for i in range(20)]
    msgs += [{"t": 100 + i, "user": f"v{i}", "text": t} for i, t in enumerate(["LMAO", "no way", "JASON COOKED", "omg", "lol", "bruh", "WTF"])]
    cc = signals.chat_curve(msgs, 0, 120)
    assert cc["spam"][1]  # bin 10-20 is giveaway spam
    assert not cc["spam"][10]
    assert cc["score"][10] > cc["score"][1]


def test_transcribe_clean():
    raw = {"segments": [{"start": 0, "end": 2, "text": "Oh shit!", "no_speech_prob": 0.0},
                        {"start": 2.1, "end": 3, "text": "Oh shit!", "no_speech_prob": 0.0},      # glitch duplicate (<0.25 s gap): dropped
                        {"start": 5.0, "end": 6, "text": "Oh shit!", "no_speech_prob": 0.0},      # real repeat after a pause: kept
                        {"start": 30, "end": 40, "text": "Thank you.", "no_speech_prob": 0.0}],   # past the window: dropped
           "words": [{"word": "Oh", "start": 0, "end": 0.5}, {"word": "shit!", "start": 0.6, "end": 5.0}, {"word": "Oh", "start": 5.0, "end": 5.3},
                     {"word": "shit!", "start": 5.4, "end": 5.9}, {"word": "Thank", "start": 30, "end": 31}]}
    out = transcribe.clean(raw, 20.0)
    assert out["text"] == "Oh shit! Oh shit!"
    assert len(out["words"]) == 4 and out["words"][1]["end"] - out["words"][1]["start"] <= 0.6


def test_merge_fills_phrases_not_strays():
    base = {"text": "a b", "words": [{"word": "a", "start": 0.0, "end": 0.3}, {"word": "b", "start": 6.0, "end": 6.3}]}
    stray = {"text": "", "words": [{"word": "x", "start": 2.0, "end": 2.2}]}
    phrase = {"text": "", "words": [{"word": f"w{i}", "start": 2.0 + i * 0.4, "end": 2.3 + i * 0.4} for i in range(3)]}
    assert transcribe.merge(base, stray)["merged_from_second_pass"] == 0
    assert transcribe.merge(base, phrase)["merged_from_second_pass"] == 3


def test_safe_zone_constants():
    from render import SAFE, ZONE, TEXT_ML, TEXT_MR, H
    assert ZONE["face_top"] >= SAFE["top"] and ZONE["hook_y"] >= SAFE["top"]
    assert H - ZONE["caption_margin_v"] <= H - SAFE["bottom"]
    assert TEXT_ML >= SAFE["left"] and TEXT_MR >= SAFE["right"]


def test_build_ass_no_overlap():
    words = [{"word": w, "start": 1 + i * 0.4, "end": 1.3 + i * 0.4} for i, w in enumerate("one two three four five six".split())]
    ass = render.build_ass(words, 0.0, 10.0, "A short hook", 2.6, "word_pop", True)
    assert "Dialogue" in ass and "Hook" in ass
    # phrase 1 last word must end before phrase 2 starts (2.2)
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue: 0")]
    ends = [float(l.split(",")[2].split(":")[-1]) for l in lines[:3]]
    assert max(ends) <= 2.21

#!/usr/bin/env python3
"""
AutoEdit - free automatic video editor.

Give it a folder (or list) of photos and videos. It will:
  * analyse every clip (sharpness, shake, faces, motion, speech, scene cuts)
  * pick the best moments and cut out boring / blurry / shaky / silent parts
  * keep talking parts whole (vlog styles) and remove long pauses
  * turn photos into moving "Ken Burns" shots (zoom & pan, face-aware)
  * fit everything to 16:9, 9:16 (Reels/TikTok/Shorts) or 1:1, with blurred
    background fill or face-aware cropping
  * colour-grade it to a style (vlog, travel, cinematic, film, reels, chill)
  * add transitions, title card, film grain, vignette, letterbox, slow motion
  * generate subtitles automatically (any language Whisper knows) and burn
    them in, plus a separate .srt file
  * add music: your own songs from the music/ folder, or a brand-new
    royalty-free track composed for the video, cut on the beat, and
    automatically lowered when someone speaks
  * normalise loudness for YouTube / Instagram / TikTok

Usage (simple):
    python autoedit.py my_trip_folder
    python autoedit.py my_trip_folder --style cinematic --title "Bali 2026"
    python autoedit.py a.mp4 b.jpg c.mov --style reels --length 30
Run "python autoedit.py --help" for every option.
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import music_gen  # noqa: E402

try:
    import cv2
except ImportError:  # analysis degrades gracefully without OpenCV
    cv2 = None

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps  # noqa: E402

try:  # iPhone HEIC photos
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".3gp", ".wmv", ".flv", ".mpg", ".mpeg"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".gif"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
FPS = 30


# ------------------------------------------------------------------ ffmpeg
def find_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
    sys.exit("ffmpeg not found. Run:  pip install imageio-ffmpeg")


FFMPEG = find_ffmpeg()
_FILTERS = None


def has_filter(name):
    global _FILTERS
    if _FILTERS is None:
        out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        _FILTERS = {ln.split()[1] for ln in out.splitlines() if len(ln.split()) > 2 and ln.startswith(" ")}
    return name in _FILTERS


def run(cmd, what="ffmpeg"):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"{what} failed:\n" + p.stderr[-3000:])
    return p


def probe(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    err = p.stderr
    info = {"duration": 0.0, "w": 0, "h": 0, "fps": 30.0, "audio": False, "video": False,
            "rotation": 0, "time": None, "hdr": False}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    for line in err.splitlines():
        if "Video:" in line and not info["video"] and "attached pic" not in line:
            info["video"] = True
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                info["w"], info["h"] = int(m[1]), int(m[2])
            m = re.search(r"([\d.]+) fps", line)
            if m:
                info["fps"] = float(m[1])
            if "arib-std-b67" in line or "smpte2084" in line:
                info["hdr"] = True
        if "Audio:" in line:
            info["audio"] = True
    m = re.search(r"rotation of (-?[\d.]+) degrees", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
    if m:
        info["rotation"] = int(float(m[1])) % 360
        if info["rotation"] in (90, 270):
            info["w"], info["h"] = info["h"], info["w"]
    m = re.search(r"creation_time\s*:\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", err)
    if m:
        try:
            info["time"] = dt.datetime.fromisoformat(m[1])
        except ValueError:
            pass
    return info


# ------------------------------------------------------------------ styles
STYLES = {
    "vlog": dict(
        desc="Bright, punchy, fast cuts, keeps people talking, upbeat music, captions",
        aspect="16:9", clip=2.6, photo=2.8, tr=0.40, mood="upbeat",
        transitions=["smoothleft", "smoothright", "slideleft", "circleopen", "fade", "zoomin", "wipeleft", "smoothup"],
        grade="eq=contrast=1.06:saturation=1.18:brightness=0.02,unsharp=5:5:0.4",
        vignette=False, grain=0, letterbox=False, voice=1.0, music=0.30, keep_speech=True,
        slowmo=False, sub_style="pill", title_font="sans"),
    "travel": dict(
        desc="Vibrant, warm, energetic, dynamic slide transitions, happy music",
        aspect="16:9", clip=2.0, photo=2.2, tr=0.35, mood="happy",
        transitions=["slideleft", "slideright", "smoothleft", "wiperight", "zoomin", "radial", "circlecrop", "hlslice"],
        grade="eq=contrast=1.08:saturation=1.32,colorbalance=rm=0.04:bm=-0.04:rh=0.03:bh=-0.03,unsharp=5:5:0.5",
        vignette=True, grain=0, letterbox=False, voice=0.8, music=0.40, keep_speech=True,
        slowmo=False, sub_style="pill", title_font="sans"),
    "cinematic": dict(
        desc="Movie look: 2.39 letterbox, teal & orange grade, slow motion, long dissolves, epic score",
        aspect="16:9", clip=4.5, photo=4.5, tr=1.0, mood="cinematic",
        transitions=["fade", "fadeblack", "dissolve", "fade", "smoothleft"],
        grade="eq=contrast=1.12:saturation=0.92:gamma=0.97,"
              "colorbalance=rs=-0.08:gs=-0.02:bs=0.10:rh=0.08:gh=0.02:bh=-0.08",
        vignette=True, grain=6, letterbox=True, voice=0.35, music=0.55, keep_speech=False,
        slowmo=True, sub_style="film", title_font="serif"),
    "film": dict(
        desc="Nostalgic memories: warm vintage film colours, grain, gentle piano",
        aspect="16:9", clip=3.5, photo=3.8, tr=0.9, mood="emotional",
        transitions=["fade", "fadeblack", "dissolve", "fadewhite"],
        grade="curves=preset=vintage,eq=saturation=0.85:contrast=1.04",
        vignette=True, grain=12, letterbox=False, voice=0.40, music=0.55, keep_speech=False,
        slowmo=True, sub_style="film", title_font="serif"),
    "reels": dict(
        desc="Vertical 9:16 for TikTok / Reels / Shorts: very fast beat cuts, big captions",
        aspect="9:16", clip=1.6, photo=1.8, tr=0.25, mood="upbeat",
        transitions=["zoomin", "slideup", "slidedown", "smoothleft", "circleopen", "pixelize", "squeezeh", "wipeup"],
        grade="eq=contrast=1.08:saturation=1.25:brightness=0.02,unsharp=5:5:0.6",
        vignette=False, grain=0, letterbox=False, voice=1.0, music=0.35, keep_speech=True,
        slowmo=False, sub_style="big", title_font="sans"),
    "chill": dict(
        desc="Soft pastel aesthetic, lo-fi beats, relaxed pacing",
        aspect="16:9", clip=3.0, photo=3.2, tr=0.7, mood="chill",
        transitions=["fade", "dissolve", "smoothleft", "smoothright", "fade"],
        grade="eq=contrast=0.95:saturation=0.88:brightness=0.03,colorbalance=bs=0.04:bh=0.03:rm=0.02",
        vignette=False, grain=4, letterbox=False, voice=0.6, music=0.45, keep_speech=True,
        slowmo=False, sub_style="pill", title_font="sans"),
}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5)}


def load_style_packs(folder=HERE / "styles"):
    """Add or override styles and music moods from styles/*.json (installed by updates or by hand)."""
    for f in sorted(Path(folder).glob("*.json")):
        try:
            pack = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"(ignoring style pack {f.name}: {e})")
            continue
        for name, mood in (pack.get("moods") or {}).items():
            parent = mood.pop("extends", "upbeat")
            base = dict(music_gen.MOODS.get(parent, music_gen.MOODS["upbeat"]))
            base.setdefault("sound", parent)
            base.update(mood)
            music_gen.MOODS[name] = base
        for name, st in (pack.get("styles") or {}).items():
            base = dict(STYLES.get(st.pop("extends", "vlog"), STYLES["vlog"]))
            base.update(st)
            used = re.findall(r"(?:^|,)\s*([a-z0-9_]+)\s*=", base.get("grade") or "")
            if any(not has_filter(u) for u in used):
                print(f"(style '{name}' needs a newer ffmpeg, skipped)")
                continue
            STYLES[name] = base


load_style_packs()


def out_size(aspect, res):
    a, b = ASPECTS[aspect]
    short = res
    if a >= b:
        w, h = round(short * a / b), short
    else:
        w, h = short, round(short * b / a)
    return w - w % 2, h - h % 2


# ------------------------------------------------------------------ fonts
FONT_CANDIDATES = {
    "cjk": [
        "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/YuGothB.ttc",
        "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc", "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ],
    "sans": [
        "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ],
    "serif": [
        "C:/Windows/Fonts/georgia.ttf", "C:/Windows/Fonts/times.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf", "/System/Library/Fonts/Times.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/TTF/DejaVuSerif.ttf",
    ],
}
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]")


def get_font(kind, size, text=""):
    user_fonts = sorted(glob.glob(str(HERE / "fonts" / "*.tt[fc]")) + glob.glob(str(HERE / "fonts" / "*.otf")))
    if CJK_RE.search(text or ""):
        kind = "cjk"
    for path in user_fonts + FONT_CANDIDATES.get(kind, []) + FONT_CANDIDATES["sans"]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ------------------------------------------------------------------ media discovery
def collect_media(inputs):
    files = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*")) if f.is_file()]
        elif p.exists():
            files.append(p)
        else:
            files += [Path(f) for f in sorted(glob.glob(item))]
    videos, photos = [], []
    for f in files:
        ext = f.suffix.lower()
        if f.name.startswith("."):
            continue
        if ext in VIDEO_EXT:
            videos.append(f)
        elif ext in PHOTO_EXT:
            photos.append(f)
    return videos, photos


def photo_time(path):
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            val = ex.get_ifd(0x8769).get(36867) or ex.get(306)
            if val:
                return dt.datetime.strptime(str(val).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


# ------------------------------------------------------------------ analysis
_FACE = None


def face_detector():
    global _FACE
    if _FACE is None and cv2 is not None:
        try:
            _FACE = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        except Exception:
            _FACE = False
    return _FACE or None


def detect_faces(gray):
    det = face_detector()
    if det is None:
        return []
    h = gray.shape[0]
    return det.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(max(16, h // 14),) * 2)


def analyse_video(path, info, rate=4.0):
    """Sample frames at `rate` Hz with ffmpeg and compute per-sample metrics."""
    aw = 320
    ah = max(2, int(round(aw * info["h"] / max(info["w"], 1) / 2)) * 2)
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-vf", f"fps={rate},scale={aw}:{ah}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fsize = aw * ah * 3
    m = {k: [] for k in ("sharp", "bright", "motion", "shake", "face", "face_x", "sat", "cut")}
    prev_g, prev_h, prev_shift = None, None, (0.0, 0.0)
    i = 0
    while True:
        buf = proc.stdout.read(fsize)
        if len(buf) < fsize:
            break
        rgb = np.frombuffer(buf, np.uint8).reshape(ah, aw, 3)
        if cv2 is not None:
            g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 4, 4], [0, 180, 0, 256, 0, 256])
            hist = cv2.normalize(hist, hist).flatten()
            sat = float(hsv[..., 1].mean() / 255)
        else:
            g = rgb.mean(axis=2).astype(np.uint8)
            sharp = float(np.var(np.diff(g.astype(float), axis=0)))
            hist = np.histogram(g, 32, (0, 255))[0].astype(float)
            hist /= hist.sum() + 1e-9
            sat = 0.3
        motion, shake, cut = 0.0, 0.0, False
        if prev_g is not None:
            motion = float(np.mean(np.abs(g.astype(np.int16) - prev_g.astype(np.int16))) / 255)
            if cv2 is not None:
                (sx, sy), _ = cv2.phaseCorrelate(prev_g.astype(np.float32), g.astype(np.float32))
                shake = math.hypot(sx - prev_shift[0], sy - prev_shift[1]) / aw
                prev_shift = (sx, sy)
                corr = cv2.compareHist(prev_h, hist, cv2.HISTCMP_CORREL)
            else:
                corr = 1 - 0.5 * np.abs(prev_h - hist).sum()
            cut = corr < 0.55 and motion > 0.06
        faces = detect_faces(g) if (i % 2 == 0) else []
        if len(faces):
            fx = float(np.mean([(x + w / 2) / aw for (x, y, w, h) in faces]))
        else:
            fx = m["face_x"][-1] if (i % 2 == 1 and m["face_x"]) else None
        nf = len(faces) if i % 2 == 0 else (m["face"][-1] if m["face"] else 0)
        for k, v in (("sharp", sharp), ("bright", float(g.mean() / 255)), ("motion", motion), ("shake", shake),
                     ("face", nf), ("face_x", fx), ("sat", sat), ("cut", cut)):
            m[k].append(v)
        if i % int(rate) == 0:  # 1 frame per second for the smart AI
            m.setdefault("thumbs", []).append(rgb.copy())
        prev_g, prev_h = g, hist
        i += 1
    proc.wait()
    m["rate"] = rate
    m["n"] = i
    return m


def extract_audio(path, out_wav, sr=16000):
    run([FFMPEG, "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "wav", str(out_wav)])


def audio_levels(wav, rate=4.0):
    import wave
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    hop = int(sr / rate)
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1))


# ------------------------------------------------------------------ speech / subtitles
_WHISPER = {}


def transcribe(wav, model_size="small", language=None, translate=False, log=print):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("  (subtitles skipped: faster-whisper is not installed)")
        return None
    key = model_size
    if key not in _WHISPER:
        log(f"  loading speech model '{model_size}' (first time downloads it once)...")
        _WHISPER[key] = None
        err = None
        # already downloaded -> works offline; otherwise download it once (needs internet)
        for kw in (dict(local_files_only=True), dict(device="cpu", local_files_only=True), {}, dict(device="cpu")):
            try:
                _WHISPER[key] = WhisperModel(model_size, compute_type="int8", **({"device": "auto"} | kw))
                break
            except Exception as e:
                err = err or e
        if _WHISPER[key] is None:
            log(f"  (subtitles skipped: speech model not available - connect to the internet once to download it: {err})")
    model = _WHISPER[key]
    if model is None:
        return None
    segs, inf = model.transcribe(str(wav), word_timestamps=True, vad_filter=True, language=language or None,
                                 task="translate" if translate else "transcribe", beam_size=5)
    out = []
    for s in segs:
        words = [dict(start=w.start, end=w.end, word=w.word) for w in (s.words or [])]
        if not words:
            words = [dict(start=s.start, end=s.end, word=s.text)]
        out.append(dict(start=s.start, end=s.end, text=s.text.strip(), words=words))
    return dict(language=inf.language, segments=out)


# ------------------------------------------------------------------ candidate selection
def sample_scores(m, levels, speech_mask, norm):
    n = m["n"]
    sharp = np.array(m["sharp"]) / norm["sharp"]
    bright = np.array(m["bright"])
    motion = np.array(m["motion"])
    shake = np.array(m["shake"])
    face = np.minimum(np.array(m["face"], dtype=float), 2)
    sat = np.array(m["sat"])
    lv = np.zeros(n)
    if len(levels):
        lv[: min(n, len(levels))] = levels[:n]
    lv = np.minimum(lv / (norm["level"] + 1e-6), 1.5)
    s = (0.45 * np.minimum(sharp, 1.5)
         + 0.25 * face
         + 0.15 * np.minimum(motion * 15, 1.0)
         + 0.15 * sat
         + 0.10 * lv
         - 3.0 * np.maximum(shake - 0.02, 0)
         - 0.8 * np.maximum(0.15 - bright, 0) * 6
         - 0.5 * np.maximum(bright - 0.92, 0) * 10)
    s[: min(2, n)] -= 0.3   # first/last half-second: pressing record
    s[max(0, n - 2):] -= 0.3
    if speech_mask is not None:
        s += 0.4 * speech_mask[:n]
    if m.get("ai") is not None:  # smart AI: what is actually in the shot
        s += 0.5 * np.asarray(m["ai"])[:n]
    return s


def make_candidates(item, style, norm):
    """Turn one analysed video into candidate clips."""
    m, rate = item["m"], item["m"]["rate"]
    n = m["n"]
    if n < 2:
        return []
    dur = item["info"]["duration"] or n / rate
    speech_mask = np.zeros(n)
    chunks = []
    tr = item.get("transcript")
    if tr and tr["segments"]:
        cur = None
        for s in tr["segments"]:
            if cur and s["start"] - cur["end"] < 0.8 and s["end"] - cur["start"] < 20:
                cur["end"] = s["end"]
                cur["text"] += " " + s["text"]
            else:
                if cur:
                    chunks.append(cur)
                cur = dict(start=s["start"], end=s["end"], text=s["text"])
        if cur:
            chunks.append(cur)
        for c in chunks:
            speech_mask[int(c["start"] * rate): int(math.ceil(c["end"] * rate)) + 1] = 1
    scores = sample_scores(m, item.get("levels", []), speech_mask, norm)
    item["scores"] = scores
    cands = []
    if style["keep_speech"]:
        for c in chunks:
            a, b = max(0.0, c["start"] - 0.2), min(dur, c["end"] + 0.35)
            if b - a < 0.8:
                continue
            ia, ib = int(a * rate), max(int(a * rate) + 1, int(b * rate))
            cands.append(dict(kind="video", path=item["path"], start=a, dur=b - a, lim_start=a, lim_end=min(dur, b + 0.6),
                              score=1.2 + float(scores[ia:ib].mean()), speech=True, item=item))
        blocked = speech_mask > 0
    else:
        blocked = np.zeros(n, bool)
    # shots from scene cuts
    cuts = [0] + [i for i, c in enumerate(m["cut"]) if c] + [n]
    L = style["clip"]
    win = max(2, int(round(L * rate)))
    for a, b in zip(cuts[:-1], cuts[1:]):
        # split shot into free (non-speech) runs
        i = a
        while i < b:
            if blocked[i]:
                i += 1
                continue
            j = i
            while j < b and not blocked[j]:
                j += 1
            run_len = j - i
            if run_len >= max(3, int(0.8 * rate)):
                seg = scores[i:j]
                w = min(win, run_len)
                cs = np.convolve(seg, np.ones(w) / w, mode="valid")
                taken = np.zeros(len(cs), bool)
                k_max = max(1, run_len // (2 * win))
                for _ in range(k_max):
                    cs_m = np.where(taken, -1e9, cs)
                    k = int(np.argmax(cs_m))
                    if cs_m[k] < -1e8:
                        break
                    taken[max(0, k - w): k + w] = True
                    st = (i + k) / rate
                    cands.append(dict(kind="video", path=item["path"], start=st, dur=w / rate,
                                      lim_start=i / rate, lim_end=min(dur, j / rate), score=float(cs[k]),
                                      speech=False, item=item))
            i = j
    # slow motion for high-motion moments (cinematic/film)
    for c in cands:
        ia, ib = int(c["start"] * rate), max(int(c["start"] * rate) + 1, int((c["start"] + c["dur"]) * rate))
        c["motion"] = float(np.mean(m["motion"][ia:ib]))
        fx = [v for v in m["face_x"][ia:ib] if v is not None]
        c["face_x"] = float(np.median(fx)) if fx else None
        c["shaky"] = float(np.mean(m["shake"][ia:ib])) > 0.012
        if m.get("ai_emb") is not None and len(m["ai_emb"]):
            ja = min(int(c["start"]), len(m["ai_emb"]) - 1)
            jb = max(ja + 1, min(int(math.ceil(c["start"] + c["dur"])), len(m["ai_emb"])))
            e = m["ai_emb"][ja:jb].mean(axis=0)
            c["emb"] = e / (np.linalg.norm(e) + 1e-9)
            labs = m["ai_label"][ja:jb]
            c["label"] = max(set(labs), key=labs.count) if labs else None
        c["time_key"] = item["time_key"]
    return cands


def dhash(img):
    g = img.convert("L").resize((9, 8), Image.BILINEAR)
    a = np.asarray(g, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def analyse_photo(path):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        small = im.copy()
        small.thumbnail((640, 640))
    arr = np.asarray(small)
    face_x = face_y = None
    sharp = 100.0
    if cv2 is not None:
        g = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
        faces = detect_faces(g)
        if len(faces):
            face_x = float(np.mean([(x + w / 2) / g.shape[1] for (x, y, w, h) in faces]))
            face_y = float(np.mean([(y + h / 2) / g.shape[0] for (x, y, w, h) in faces]))
    thumb = small.copy()
    thumb.thumbnail((320, 320))
    return dict(size=im.size, sharp=sharp, face_x=face_x, face_y=face_y, hash=dhash(small), thumb=thumb)


# ------------------------------------------------------------------ rendering: clips
def fit_filter(src_w, src_h, W, H, face_x=None):
    """Return a filter that fits a source of src_w x src_h into W x H."""
    sa, ta = src_w / max(src_h, 1), W / H
    if abs(sa / ta - 1) < 0.18:
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if face_x is not None and sa > ta:  # landscape -> portrait: follow the face
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:x='min(max({face_x:.3f}*iw-{W}/2,0),iw-{W})':y=(ih-{H})/2")
    return (f"split=2[bga][fga];[bga]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"scale={W // 8}:{H // 8},gblur=sigma=6,scale={W}:{H},eq=brightness=-0.10:saturation=1.1[bgb];"
            f"[fga]scale={W}:{H}:force_original_aspect_ratio=decrease[fgb];[bgb][fgb]overlay=(W-w)/2:(H-h)/2")


HDR_FIX = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def render_video_clip(c, out, W, H, style, tr, enc):
    info = c["item"]["info"]
    slow = c.get("slow", 1.0)
    src_len = c["dur"] / slow
    chain = []
    if info.get("hdr") and has_filter("zscale") and has_filter("tonemap"):
        chain.append(HDR_FIX)
    if c.get("shaky") and has_filter("deshake"):
        chain.append("deshake")
    chain.append(fit_filter(info["w"], info["h"], W, H, c.get("face_x") if W < H or W == H else None))
    if slow != 1.0:
        chain.append(f"setpts={slow:.3f}*PTS")
    chain.append(f"fps={FPS},setsar=1")
    if style["grade"]:
        chain.append(style["grade"])
    chain.append("format=yuv420p")
    vf = "[0:v]" + ",".join(chain) + "[v]"
    vol = style["voice"] if (info["audio"] and slow == 1.0) else 0.0
    fin = min(tr, c["dur"] / 3) if c.get("index", 0) > 0 else 0.05
    fout = min(tr, c["dur"] / 3)
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{c['start']:.3f}", "-t", f"{src_len + 0.1:.3f}", "-i", str(c["path"])]
    if vol > 0:
        af = (f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol},"
              f"apad,atrim=0:{c['dur']:.3f},afade=t=in:d={fin:.2f},"
              f"afade=t=out:st={c['dur'] - fout:.3f}:d={fout:.2f}[a]")
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        af = f"[1:a]atrim=0:{c['dur']:.3f}[a]"
    cmd += ["-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]", "-t", f"{c['dur']:.3f}",
            "-r", str(FPS)] + enc + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)]
    run(cmd, f"rendering clip from {Path(c['path']).name}")


def compose_photo(path, W, H, scale, face=None):
    """Build a background-filled canvas of (W*scale, H*scale) for Ken Burns."""
    CW, CH = int(W * scale), int(H * scale)
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    sa, ta = im.width / im.height, W / H
    if abs(sa / ta - 1) < 0.22:
        cover = ImageOps.fit(im, (CW, CH), Image.LANCZOS,
                             centering=(face[0] if face and face[0] is not None else 0.5,
                                        face[1] if face and face[1] is not None else 0.5))
        return cover
    bg = ImageOps.fit(im, (CW // 8, CH // 8), Image.BILINEAR).filter(ImageFilter.GaussianBlur(4))
    bg = bg.resize((CW, CH), Image.BILINEAR)
    bg = Image.eval(bg, lambda v: int(v * 0.82))
    fg = im.copy()
    fg.thumbnail((int(CW * 0.94), int(CH * 0.94)), Image.LANCZOS)
    shadow = Image.new("L", (fg.width + 60, fg.height + 60), 0)
    ImageDraw.Draw(shadow).rectangle([30, 30, fg.width + 30, fg.height + 30], fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    ox, oy = (CW - fg.width) // 2, (CH - fg.height) // 2
    bg.paste((0, 0, 0), (ox - 30 + 8, oy - 30 + 12), shadow)
    bg.paste(fg, (ox, oy))
    return bg


def render_photo_clip(c, out, W, H, style, tr, enc, rng):
    scale = 1.5
    face = (c["meta"]["face_x"], c["meta"]["face_y"])
    canvas = compose_photo(c["path"], W, H, scale, face)
    src = np.asarray(canvas)
    CW, CH = canvas.size
    n = max(2, int(round(c["dur"] * FPS)))
    move = rng.choice(["in", "out", "left", "right", "in"])
    z_lo, z_hi = 1.0, 1.0 + (0.10 if style["clip"] < 3 else 0.14)
    fx = face[0] if face[0] is not None else 0.5
    fy = face[1] if face[1] is not None else 0.45

    def view(p):
        e = p * p * (3 - 2 * p)  # ease in-out
        if move == "in":
            z, cx, cy = z_lo + (z_hi - z_lo) * e, 0.5 + (fx - 0.5) * 0.5 * e, 0.5 + (fy - 0.5) * 0.5 * e
        elif move == "out":
            z, cx, cy = z_hi - (z_hi - z_lo) * e, 0.5 + (fx - 0.5) * 0.5 * (1 - e), 0.5 + (fy - 0.5) * 0.5 * (1 - e)
        else:
            z = z_hi
            span = (1 - 1 / z) / 2
            cx = 0.5 + (-span + 2 * span * e) * (1 if move == "right" else -1)
            cy = 0.5
        return z, cx, cy

    vf = f"fps={FPS},setsar=1" + ("," + style["grade"] if style["grade"] else "") + ",format=yuv420p"
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf, "-map", "0:v", "-map", "1:a",
           "-t", f"{c['dur']:.3f}"] + enc + ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for k in range(n):
            z, cx, cy = view(k / (n - 1))
            vw, vh = CW / z, CH / z
            x0 = min(max(cx * CW - vw / 2, 0), CW - vw)
            y0 = min(max(cy * CH - vh / 2, 0), CH - vh)
            sx, sy = W / vw, H / vh
            if cv2 is not None:
                Mx = np.array([[sx, 0, -x0 * sx], [0, sy, -y0 * sy]], dtype=np.float32)
                frame = cv2.warpAffine(src, Mx, (W, H), flags=cv2.INTER_AREA if sx < 1 else cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_REFLECT)
            else:
                frame = np.asarray(canvas.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh)))
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise RuntimeError(f"rendering photo {Path(c['path']).name} failed:\n{err[-2000:]}")


# ------------------------------------------------------------------ text overlays
def draw_text_png(path, W, H, text, kind="sub", style_name="pill", font_kind="sans", subtitle=None, letterbox=0):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if not text:
        img.save(path)
        return
    d = ImageDraw.Draw(img)
    short = min(W, H)
    if kind == "title":
        size = int(short * (0.11 if W >= H else 0.085))
        font = get_font(font_kind, size, text)
        lines = wrap(d, text, font, W * 0.86)
        sub_font = get_font(font_kind, int(size * 0.36), subtitle or "")
        total_h = len(lines) * size * 1.15 + (size * 0.7 if subtitle else 0)
        y = H / 2 - total_h / 2
        for ln in lines:
            tw = d.textlength(ln, font=font)
            d.text(((W - tw) / 2 + 3, y + 4), ln, font=font, fill=(0, 0, 0, 110))
            d.text(((W - tw) / 2, y), ln, font=font, fill=(255, 255, 255, 255))
            y += size * 1.15
        if subtitle:
            sub = subtitle.upper() if font_kind == "serif" else subtitle
            tw = d.textlength(sub, font=sub_font)
            y += size * 0.15
            d.line([(W / 2 - tw / 2, y), (W / 2 + tw / 2, y)], fill=(255, 255, 255, 180), width=max(1, short // 400))
            d.text(((W - tw) / 2, y + size * 0.15), sub, font=sub_font, fill=(255, 255, 255, 230))
        img.save(path)
        return
    # subtitles
    big = style_name == "big"
    size = int(short * (0.062 if big else 0.048))
    font = get_font("sans", size, text)
    lines = wrap(d, text, font, W * (0.80 if big else 0.84))[:3]
    lh = size * 1.25
    block_h = lh * len(lines)
    if big:
        y = H * 0.66 - block_h / 2
    else:
        y = H - max(letterbox, 0) - H * 0.06 - block_h
    for ln in lines:
        tw = d.textlength(ln, font=font)
        x = (W - tw) / 2
        if style_name == "pill":
            pad = size * 0.35
            d.rounded_rectangle([x - pad, y - pad * 0.45, x + tw + pad, y + size + pad * 0.75],
                                radius=int(size * 0.35), fill=(0, 0, 0, 150))
            d.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        elif big:
            d.text((x, y), ln, font=font, fill=(255, 255, 255, 255), stroke_width=max(3, size // 9),
                   stroke_fill=(0, 0, 0, 255))
        else:  # film: clean white with soft outline
            d.text((x, y), ln, font=font, fill=(255, 255, 255, 240), stroke_width=max(2, size // 16),
                   stroke_fill=(0, 0, 0, 200))
        y += lh
    img.save(path)


def wrap(d, text, font, max_w):
    if d.textlength(text, font=font) <= max_w:
        return [text]
    cjk = bool(CJK_RE.search(text)) and " " not in text.strip()
    tokens = list(text) if cjk else text.split(" ")
    lines, cur = [], ""
    for t in tokens:
        trial = (cur + t) if cjk else (cur + " " + t).strip()
        if d.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = t
    if cur:
        lines.append(cur)
    return lines


def build_sub_lines(words, aspect_vertical):
    """Group timed words into readable subtitle lines."""
    maxc = 30 if aspect_vertical else 46
    lines, cur = [], []

    def flush():
        if cur:
            txt = "".join(w["word"] for w in cur).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt:
                lines.append(dict(start=cur[0]["start"], end=cur[-1]["end"], text=txt))
            cur.clear()

    for w in words:
        if cur:
            txt = "".join(x["word"] for x in cur)
            n_chars = len(txt) * (2 if CJK_RE.search(txt) else 1)
            gap = w["start"] - cur[-1]["end"]
            if (n_chars > maxc or gap > 0.7 or w["end"] - cur[0]["start"] > 4.0
                    or (re.search(r"[.?!。？！]$", txt.strip()) and cur[-1]["end"] - cur[0]["start"] > 0.8)):
                flush()
        cur.append(w)
    flush()
    for a, b in zip(lines, lines[1:]):
        a["end"] = min(a["end"] + 0.25, b["start"])
    if lines:
        lines[-1]["end"] += 0.25
    return [ln for ln in lines if ln["end"] - ln["start"] > 0.2]


def srt_time(t):
    t = max(0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"


# ------------------------------------------------------------------ music
def detect_beat(wav_path):
    """Very small tempo detector: returns (beat_seconds, first_beat_offset)."""
    import wave
    with wave.open(str(wav_path)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(min(w.getnframes(), sr * 90)), "<i2").astype(np.float32)
    x = x.reshape(-1, ch).mean(axis=1) / 32768
    hop = 512
    n = len(x) // hop - 1
    if n < 100:
        return 0.5, 0.0
    frames = np.lib.stride_tricks.sliding_window_view(x, 1024)[::hop][:n] * np.hanning(1024)
    spec = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.maximum(np.diff(np.log1p(spec), axis=0), 0).sum(axis=1)
    flux -= flux.mean()
    fr = sr / hop
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lo, hi = int(fr * 60 / 180), int(fr * 60 / 70)
    lag = lo + int(np.argmax(ac[lo:hi]))
    beat = lag / fr
    phases = [flux[p::lag].sum() for p in range(lag)]
    return beat, int(np.argmax(phases)) / fr


def find_music(mood):
    folder = HERE / "music"
    if not folder.exists():
        return None
    files = [f for f in folder.rglob("*") if f.suffix.lower() in AUDIO_EXT]
    if not files:
        return None
    tagged = [f for f in files if mood in (f.parent.name.lower() + " " + f.stem.lower())]
    untagged = [f for f in files if f.parent == folder and not any(mm in f.stem.lower() for mm in music_gen.MOODS)]
    pool = tagged or untagged
    return random.choice(pool) if pool else None


# ------------------------------------------------------------------ main pipeline
def make_video(inputs, output=None, style="auto", aspect=None, length=None, title=None, subtitle_text=None,
               music=None, mood=None, subtitles=True, sub_language=None, translate=False, whisper_model="small",
               resolution=1080, seed=None, keep_temp=False, music_volume=None, progress=None, log=print,
               smart=True, focus=None):
    t_start = time.time()
    rng = random.Random(seed if seed is not None else int(time.time()))

    def prog(frac, msg):
        log(msg)
        if progress:
            try:
                progress(frac, msg)
            except Exception:
                pass

    videos, photos = collect_media(inputs)
    if not videos and not photos:
        raise ValueError("No photos or videos found. Supported: " + ", ".join(sorted(VIDEO_EXT | PHOTO_EXT)))
    prog(0.02, f"Found {len(videos)} video(s) and {len(photos)} photo(s)")

    out_dir = Path(output).parent if output else HERE / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="autoedit_", dir=out_dir))

    # ---- analyse
    items = []
    for i, v in enumerate(videos):
        prog(0.03 + 0.25 * i / max(1, len(videos)), f"Analysing video {i + 1}/{len(videos)}: {v.name}")
        info = probe(v)
        if not info["video"] or info["duration"] < 0.5:
            log(f"  skipping {v.name} (no usable video)")
            continue
        it = dict(kind="video", path=v, info=info, time=info["time"])
        it["m"] = analyse_video(v, info)
        if info["audio"]:
            wav = work / f"a{i}.wav"
            try:
                extract_audio(v, wav)
                it["wav"] = wav
                it["levels"] = audio_levels(wav)
            except RuntimeError:
                pass
        items.append(it)
    photo_items = []
    for p in photos:
        try:
            meta = analyse_photo(p)
        except Exception as e:
            log(f"  skipping {p.name} ({e})")
            continue
        photo_items.append(dict(kind="photo", path=p, meta=meta, time=photo_time(p)))
    # ---- smart AI: understand what is in every shot (free, runs on this computer)
    if smart:
        prog(0.28, "Smart AI is looking at your shots" + (f" (focus: {focus})" if focus else ""))
        try:
            import ai_vision
            eye = ai_vision.load(focus, log)
        except Exception as e:
            eye = None
            log(f"  (smart AI skipped: {e})")
        if eye is not None:
            try:
                for it in items:
                    thumbs = it["m"].pop("thumbs", [])
                    emb = eye.embed_images(thumbs)
                    sc, labels = eye.score(emb)
                    n = it["m"]["n"]
                    if len(sc):
                        t1 = np.arange(len(sc)) * it["m"]["rate"]
                        it["m"]["ai"] = np.interp(np.arange(n), t1, sc)
                        it["m"]["ai_emb"], it["m"]["ai_label"] = emb, labels
                if photo_items:
                    emb = eye.embed_images([ph["meta"]["thumb"] for ph in photo_items])
                    sc, labels = eye.score(emb)
                    for ph, e, v, lab in zip(photo_items, emb, sc, labels):
                        ph["meta"].update(emb=e, ai=float(v), label=lab)
                    # drop accidental photos (floor, pocket, screenshots) when there are enough good ones
                    good = [ph for ph in photo_items if ph["meta"]["ai"] > -0.6]
                    if len(good) >= 3 and len(good) < len(photo_items):
                        log(f"  smart AI left out {len(photo_items) - len(good)} accidental-looking photo(s)")
                        photo_items = good
            except Exception as e:
                log(f"  (smart AI skipped: {e})")
    for it in items:
        it["m"].pop("thumbs", None)

    # drop near-duplicate photos (bursts) - keep the sharpest
    kept = []
    for ph in sorted(photo_items, key=lambda x: -x["meta"]["sharp"]):
        if all(np.count_nonzero(ph["meta"]["hash"] != k["meta"]["hash"]) > 6 and
               not ("emb" in ph["meta"] and "emb" in k["meta"] and float(ph["meta"]["emb"] @ k["meta"]["emb"]) > 0.97)
               for k in kept):
            kept.append(ph)
    if len(kept) < len(photo_items):
        log(f"  removed {len(photo_items) - len(kept)} near-duplicate photo(s)")
    photo_items = kept

    # ---- chronological order
    allm = items + photo_items
    with_time = [x for x in allm if x["time"]]
    use_time = len(with_time) >= 0.8 * len(allm)
    ordered = sorted(allm, key=lambda x: (x["time"] or dt.datetime.max, natural_key(x["path"].name))) if use_time \
        else sorted(allm, key=lambda x: natural_key(x["path"].name))
    for k, x in enumerate(ordered):
        x["time_key"] = k

    # ---- speech
    want_speech = subtitles or style in ("vlog", "reels", "chill", "travel", "auto")
    speech_total = 0.0
    if want_speech:
        talk_items = [x for x in items if x.get("wav") is not None and len(x.get("levels", [])) and
                      float(np.percentile(x["levels"], 90)) > 0.01]
        for i, it in enumerate(talk_items):
            prog(0.30 + 0.20 * i / max(1, len(talk_items)), f"Listening for speech {i + 1}/{len(talk_items)}: {it['path'].name}")
            tr = transcribe(it["wav"], whisper_model, sub_language, translate, log)
            if tr is None:
                break
            it["transcript"] = tr
            speech_total += sum(s["end"] - s["start"] for s in tr["segments"])

    # ---- style
    total_video = sum(x["info"]["duration"] for x in items)
    if style == "auto" or style not in STYLES:
        if photo_items and len(photo_items) >= 1.5 * max(1, len(items)):
            style = "film"
        elif total_video and speech_total / total_video > 0.3:
            style = "vlog"
        elif items and np.mean([np.mean(x["m"]["motion"] or [0]) for x in items]) > 0.05:
            style = "travel"
        else:
            style = "cinematic"
        log(f"Auto-picked style: {style}")
    S = dict(STYLES[style])
    if music_volume is not None:
        S["music"] = music_volume
    aspect = aspect or S["aspect"]
    W, H = out_size(aspect, resolution)
    tr = S["tr"]
    mood = mood or S["mood"]

    # ---- candidates
    all_sharp = np.concatenate([np.array(x["m"]["sharp"]) for x in items]) if items else np.array([100.0])
    all_lv = np.concatenate([x["levels"] for x in items if len(x.get("levels", []))] or [np.array([0.05])])
    norm = dict(sharp=float(np.percentile(all_sharp, 75)) + 1e-6, level=float(np.percentile(all_lv, 90)) + 1e-6)
    cands = []
    for it in items:
        cands += make_candidates(it, S, norm)

    # ---- duration budget
    P = S["photo"]
    speech_c = [c for c in cands if c["speech"]]
    if length:
        target = float(length)
    else:
        free_video = total_video - (speech_total if S["keep_speech"] else 0)
        target = len(photo_items) * P + min(sum(c["dur"] for c in speech_c), 150) \
            + min(0.35 * max(0, free_video), 90)
        target = max(target, len(photo_items) * P + len(items) * S["clip"])
        target = float(np.clip(target, 10, 300))
    # photos
    if photo_items and len(photo_items) * P > 0.85 * target and (items or length):
        max_ph = max(1, int(0.85 * target / max(1.4, P * 0.7)))
        P = max(1.4, min(P, 0.85 * target / len(photo_items)))
        if len(photo_items) > max_ph:
            ps = np.array([x["meta"]["sharp"] for x in photo_items])
            ps = ps / (np.percentile(ps, 75) + 1e-6)
            rank = {id(x): min(v, 1.5) + x["meta"].get("ai", 0.0) for x, v in zip(photo_items, ps)}
            photo_items = sorted(photo_items, key=lambda x: -rank[id(x)])[:max_ph]
    budget = target - len(photo_items) * (P - tr) - tr
    # videos: best clip of every video first, then by score
    chosen, used = [], 0.0
    by_item = {}
    for c in cands:
        by_item.setdefault(id(c["item"]), []).append(c)
    firsts = [max(cs, key=lambda c: c["score"]) for cs in by_item.values()]
    if len(firsts) > 1:  # don't force in a clip from a video that is all dark / blurry / shaky
        firsts = [c for c in firsts if c["score"] > -0.2] or firsts[:1]
    rest = sorted([c for c in cands if not any(c is f for f in firsts)], key=lambda c: -c["score"])
    speech_cap = 0.7 * budget
    speech_used = 0.0
    queue = sorted(firsts, key=lambda c: -c["score"]) + rest
    qi = 0
    while qi < len(queue):
        c = queue[qi]
        qi += 1
        d_eff = c["dur"] - tr
        if used + d_eff > budget + 0.5 and chosen:
            continue
        if c["speech"] and speech_used + c["dur"] > speech_cap and speech_used > 0:
            continue
        if any(o["path"] == c["path"] and o["start"] < c["start"] + c["dur"] and c["start"] < o["start"] + o["dur"]
               for o in chosen):
            continue
        if not c["speech"] and c.get("emb") is not None and not c.get("_again") and any(
                o.get("emb") is not None and float(o["emb"] @ c["emb"]) > 0.94 for o in chosen):
            c["_again"] = True
            queue.append(c)  # looks the same as a shot we already have - only used if time is left
            continue
        chosen.append(c)
        used += d_eff
        if c["speech"]:
            speech_used += c["dur"]
    for ph in photo_items:
        chosen.append(dict(kind="photo", path=ph["path"], meta=ph["meta"], dur=P, time_key=ph["time_key"],
                           speech=False, start=0.0, score=0, label=ph["meta"].get("label")))
    if not chosen:
        raise ValueError("Nothing usable was found in the media.")
    chosen.sort(key=lambda c: (c["time_key"], c.get("start", 0)))

    # slow motion for action shots in cinematic styles
    if S["slowmo"]:
        for c in chosen:
            if c["kind"] == "video" and not c["speech"] and c.get("motion", 0) > 0.035:
                c["slow"] = 1.6
                c["dur"] = min(c["dur"] * 1.6, S["clip"] * 1.4)

    # ---- music & beat grid
    prog(0.52, "Choosing music")
    music_path = Path(music) if music else find_music(mood)
    beat, beat_off = None, 0.0
    if music_path and music_path.exists():
        mwav = work / "music_src.wav"
        run([FFMPEG, "-y", "-v", "error", "-i", str(music_path), "-ac", "2", "-ar", "44100", str(mwav)])
        beat, beat_off = detect_beat(mwav)
        log(f"  using your music: {music_path.name} (beat {60 / beat:.0f} bpm)")
    else:
        music_path = None
        beat = 60.0 / music_gen.MOODS.get(mood, music_gen.MOODS["upbeat"])["bpm"]
        log(f"  composing a new royalty-free '{mood}' track ({60 / beat:.0f} bpm)")

    # ---- snap cuts to the beat
    t = 0.0
    n = len(chosen)
    for i, c in enumerate(chosen):
        c["index"] = i
        if i == n - 1:
            c["dur"] = max(c["dur"], 2.0) if c["kind"] == "photo" else c["dur"]
            break
        if c["kind"] == "photo":
            d_min, d_max = max(1.0, 2 * tr + 0.4), 99
        else:
            slow = c.get("slow", 1.0)
            avail = (c["lim_end"] - c["start"]) * slow
            d_min = max(0.8, 2 * tr + 0.3) if not c["speech"] else c["dur"]
            d_max = min(avail, c["dur"] + (0.6 if c["speech"] else 2.0)) if c["speech"] else avail
            d_min = min(d_min, d_max)
        mid = t + c["dur"] - tr / 2 - beat_off
        best = c["dur"]
        if beat and beat > 0.2:
            opts = []
            k0 = math.floor(mid / beat)
            for k in range(k0 - 2, k0 + 3):
                d = k * beat + beat_off - t + tr / 2
                if d_min - 1e-6 <= d <= d_max + 1e-6:
                    opts.append(d)
            if opts:
                best = min(opts, key=lambda d: abs(d - c["dur"]))
        c["dur"] = max(best, 0.5)
        t += c["dur"] - tr
    total = t + chosen[-1]["dur"]
    prog(0.55, f"Story: {n} shots, {total:.1f}s, style '{style}', {W}x{H}")
    for c in chosen:
        what = f"{c['start']:.1f}s-{c['start'] + c['dur'] / c.get('slow', 1.0):.1f}s" if c["kind"] == "video" else "photo"
        log(f"  - {Path(c['path']).name} [{what}]{' (talking)' if c['speech'] else ''}"
            f"{' (slow-motion)' if c.get('slow', 1.0) != 1.0 else ''}"
            f"{' - looks like: ' + c['label'] if c.get('label') else ''}")

    # ---- render clips
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p"]
    paths = [work / f"clip_{i:03d}.mp4" for i in range(n)]
    done = [0]

    def job(i):
        c = chosen[i]
        if c["kind"] == "photo":
            render_photo_clip(c, paths[i], W, H, S, tr, enc, random.Random(rng.random() + i))
        else:
            render_video_clip(c, paths[i], W, H, S, tr, enc)
        done[0] += 1
        prog(0.55 + 0.30 * done[0] / n, f"Rendered shot {done[0]}/{n}")

    workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(job, range(n)))

    # ---- subtitles on the final timeline
    sub_lines = []
    language = None
    offsets = []
    acc = 0.0
    for c in chosen:
        offsets.append(acc)
        acc += c["dur"] - tr
    if subtitles:
        for c, off in zip(chosen, offsets):
            trn = c.get("item", {}).get("transcript") if c["kind"] == "video" else None
            if not trn or c.get("slow", 1.0) != 1.0:
                continue
            language = language or trn["language"]
            src_a, src_b = c["start"], c["start"] + c["dur"]
            words = [w for s in trn["segments"] for w in s["words"]
                     if w["start"] >= src_a - 0.05 and w["end"] <= src_b + 0.15]
            if not words:
                continue
            mapped = [dict(start=off + w["start"] - src_a, end=min(off + c["dur"], off + w["end"] - src_a),
                           word=w["word"]) for w in words]
            sub_lines += build_sub_lines(mapped, H > W)
        sub_lines.sort(key=lambda s: s["start"])
        for a, b in zip(sub_lines, sub_lines[1:]):
            a["end"] = min(a["end"], b["start"])

    # ---- music track
    prog(0.86, "Mixing music")
    mus_wav = work / "music.wav"
    if music_path:
        run([FFMPEG, "-y", "-v", "error", "-stream_loop", "-1", "-i", str(work / "music_src.wav"), "-t", f"{total:.3f}",
             "-af", f"afade=t=in:d=0.3,afade=t=out:st={max(0, total - 3):.3f}:d=3", str(mus_wav)])
    else:
        data, sr, _, _ = music_gen.generate(total, mood, seed=rng.randint(0, 1 << 30))
        music_gen.write_wav(mus_wav, data, sr)

    # ---- overlays
    letterbox_h = 0
    if S["letterbox"] and W > H:
        letterbox_h = int(round((H - W / 2.39) / 2))
    extra_inputs = []
    title_dur = 0.0
    if title:
        title_png = work / "title.png"
        sub_txt = subtitle_text
        if sub_txt is None:
            d0 = next((x["time"] for x in ordered if x["time"]), None)
            sub_txt = d0.strftime("%d %B %Y").lstrip("0") if d0 else ""
        draw_text_png(title_png, W, H, title, kind="title", font_kind=S["title_font"], subtitle=sub_txt)
        title_dur = min(4.0, max(2.5, total * 0.25))
        extra_inputs.append(("title", title_png))
    subs_list = None
    if sub_lines:
        sub_dir = work / "subs"
        sub_dir.mkdir()
        blank = sub_dir / "blank.png"
        draw_text_png(blank, W, H, "")
        entries = []
        cur = 0.0
        for k, ln in enumerate(sub_lines):
            if ln["start"] > cur + 0.01:
                entries.append((blank, ln["start"] - cur))
            png = sub_dir / f"s{k:04d}.png"
            draw_text_png(png, W, H, ln["text"], style_name=S["sub_style"], letterbox=letterbox_h)
            entries.append((png, ln["end"] - max(ln["start"], cur)))
            cur = ln["end"]
        entries.append((blank, max(0.1, total - cur)))
        subs_list = work / "subs.txt"
        with open(subs_list, "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            for p, d in entries:
                f.write(f"file '{p.as_posix()}'\nduration {max(d, 0.04):.3f}\n")
            f.write(f"file '{blank.as_posix()}'\n")

    # ---- final assembly
    prog(0.88, "Putting it all together (transitions, effects, subtitles, music)")
    cmd = [FFMPEG, "-y", "-v", "error"]
    for p in paths:
        cmd += ["-i", str(p)]
    mi = n
    cmd += ["-i", str(mus_wav)]
    idx = n + 1
    fc = []
    for i in range(n):
        fc.append(f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p[v{i}]")
    last = "v0"
    for i in range(1, n):
        trans = rng.choice(S["transitions"])
        fc.append(f"[{last}][v{i}]xfade=transition={trans}:duration={tr:.3f}:offset={offsets[i]:.3f}[x{i}]")
        last = f"x{i}"
    post = []
    if S["vignette"]:
        post.append("vignette=angle=PI/5")
    if S["grain"]:
        post.append(f"noise=alls={S['grain']}:allf=t")
    if letterbox_h > 0:
        post.append(f"drawbox=x=0:y=0:w=iw:h={letterbox_h}:color=black:t=fill,"
                    f"drawbox=x=0:y=ih-{letterbox_h}:w=iw:h={letterbox_h}:color=black:t=fill")
    post.append("fade=t=in:st=0:d=0.6")
    post.append(f"fade=t=out:st={max(0, total - 1.2):.3f}:d=1.2")
    fc.append(f"[{last}]" + ",".join(post) + "[vb]")
    last = "vb"
    for kind, p in extra_inputs:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{title_dur:.2f}", "-i", str(p)]
        fc.append(f"[{idx}:v]format=rgba,fade=t=in:st=0.4:d=0.7:alpha=1,"
                  f"fade=t=out:st={title_dur - 0.9:.2f}:d=0.7:alpha=1[t{idx}]")
        fc.append(f"[{last}][t{idx}]overlay=0:0:eof_action=pass[o{idx}]")
        last = f"o{idx}"
        idx += 1
    if subs_list:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(subs_list)]
        fc.append(f"[{idx}:v]format=rgba[subs]")
        fc.append(f"[{last}][subs]overlay=0:0:eof_action=pass:repeatlast=1[os]")
        last = "os"
        idx += 1
    fc.append(f"[{last}]format=yuv420p[vout]")
    # audio
    has_voice = any(c["kind"] == "video" and c["item"]["info"]["audio"] and c.get("slow", 1.0) == 1.0
                    and S["voice"] > 0 for c in chosen)
    for i, off in enumerate(offsets):
        ms = int(round(off * 1000))
        fc.append(f"[{i}:a]adelay={ms}|{ms}[a{i}]")
    fc.append("".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0:dropout_transition=0[voice]"
              if n > 1 else "[a0]anull[voice]")
    mv = S["music"]
    fc.append(f"[{mi}:a]aresample=48000,volume={mv}[mus]")
    if has_voice:
        fc.append("[voice]asplit=2[vk][vs]")
        fc.append("[mus][vs]sidechaincompress=threshold=0.015:ratio=10:attack=15:release=450:makeup=1[duck]")
        fc.append("[vk][duck]amix=inputs=2:normalize=0:dropout_transition=0[mix]")
    else:
        fc.append("[voice]anullsink")
        fc.append("[mus]anull[mix]")
    fc.append(f"[mix]atrim=0:{total:.3f},afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5,"
              f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    if output is None:
        safe = re.sub(r"[^\w\-]+", "_", title or "my_video").strip("_") or "my_video"
        output = out_dir / f"{safe}_{style}_{dt.datetime.now():%Y%m%d_%H%M%S}.mp4"
    output = Path(output)
    script = work / "filter.txt"
    script.write_text(";\n".join(fc), encoding="utf-8")
    cmd += ["-filter_complex_script" if not _ffmpeg_v7() else "-/filter_complex", str(script),
            "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(output)]
    run(cmd, "final render")

    srt_path = None
    if sub_lines:
        srt_path = output.with_suffix(".srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            for k, ln in enumerate(sub_lines, 1):
                f.write(f"{k}\n{srt_time(ln['start'])} --> {srt_time(ln['end'])}\n{ln['text']}\n\n")
    if not keep_temp:
        shutil.rmtree(work, ignore_errors=True)
    secs = time.time() - t_start
    summary = dict(output=str(output), srt=str(srt_path) if srt_path else None, style=style, mood=mood,
                   music=(music_path.name if music_path else f"new royalty-free {mood} track"),
                   shots=n, duration=round(total, 1), size=f"{W}x{H}", subtitles=len(sub_lines),
                   language=language, render_seconds=round(secs, 1))
    prog(1.0, f"Done in {secs:.0f}s -> {output}")
    return summary


def _ffmpeg_v7():
    out = subprocess.run([FFMPEG, "-hide_banner", "-version"], capture_output=True, text=True).stdout
    m = re.search(r"version (?:n)?(\d+)", out)
    return bool(m and int(m[1]) >= 7)


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="AutoEdit - free automatic video editor",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Styles:\n" + "\n".join(f"  {k:10s} {v['desc']}" for k, v in STYLES.items()))
    ap.add_argument("inputs", nargs="+", help="folder(s) or files with your photos and videos")
    ap.add_argument("-o", "--output", help="output .mp4 path (default: output/<title>_<style>_<time>.mp4)")
    ap.add_argument("-s", "--style", default="auto", choices=["auto"] + list(STYLES))
    ap.add_argument("-a", "--aspect", choices=list(ASPECTS), help="16:9 (YouTube), 9:16 (TikTok/Reels), 1:1, 4:5")
    ap.add_argument("-l", "--length", type=float, help="target length in seconds (default: automatic)")
    ap.add_argument("-t", "--title", help="title shown at the start")
    ap.add_argument("--subtitle", dest="subtitle_text", help="small line under the title (default: the date)")
    ap.add_argument("-m", "--music", help="music file to use (default: from music/ folder, else composes one)")
    ap.add_argument("--mood", choices=list(music_gen.MOODS), help="music mood (default: from style)")
    ap.add_argument("--music-volume", type=float, help="0.0-1.0 (default depends on style)")
    ap.add_argument("--no-subs", action="store_true", help="do not add subtitles")
    ap.add_argument("--lang", help="spoken language code, e.g. en, zh, ms, ja, ko (default: auto-detect)")
    ap.add_argument("--translate", action="store_true", help="translate subtitles into English")
    ap.add_argument("--whisper", default="small", help="speech model: tiny, base, small, medium, large-v3")
    ap.add_argument("-r", "--resolution", type=int, default=1080, help="720, 1080 or 2160")
    ap.add_argument("--seed", type=int, help="same seed = same edit")
    ap.add_argument("--keep-temp", action="store_true")
    ap.add_argument("--focus", help='what the smart AI should favour, e.g. "food, beach, my dog"')
    ap.add_argument("--no-ai", action="store_true", help="turn off the smart AI")
    a = ap.parse_args()
    s = make_video(a.inputs, a.output, a.style, a.aspect, a.length, a.title, a.subtitle_text, a.music, a.mood,
                   not a.no_subs, a.lang, a.translate, a.whisper, a.resolution, a.seed, a.keep_temp, a.music_volume,
                   smart=not a.no_ai, focus=a.focus)
    print(json.dumps(s, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

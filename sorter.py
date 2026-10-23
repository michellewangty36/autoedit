"""
AutoEdit photo sorter: tidies a messy phone album into folders on your computer.

  - Reads the date and place saved inside every photo and video (and the date in WhatsApp file names).
  - Trips away from home get their own folder, e.g. "2026/Singapore Trip 23-26.10.2026".
  - Everything else goes into month folders, e.g. "2026/2026-10 October". Screenshots go to "Screenshots".
  - Only copies: your originals are never moved or deleted. Running it again only adds new photos.
  - You can rename any folder; new photos from the same trip still find it.
  - Works offline: place names come from a built-in list of world towns (GeoNames, CC BY 4.0).

Command line:  python sorter.py --source "C:\\Users\\me\\OneDrive\\Pictures" --library "D:\\Photo Albums"
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SETTINGS = HERE / "output" / "sorter-settings.json"
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".dng", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg"}
SKIP_PARTS = ("sticker", ".thumbnail", "thumbnails", "cache", ".trash", "$recycle.bin", "animated gifs",
              "wallpaper", "statuses", ".statuses")
STATE_NAME = ".autoedit-sorted.json"
MARK_NAME = ".album.json"
AWAY_KM = 80          # further than this from home counts as a trip
LOCK = threading.Lock()
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = "ffmpeg"


# ------------------------------------------------------------------ places (offline reverse geocoding)
class Places:
    def __init__(self):
        rows = []
        with gzip.open(DATA / "places.csv.gz", "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for lat, lon, name, admin1, cc in r:
                rows.append((float(lat), float(lon), name, admin1, cc))
        self.rows = rows
        ll = np.radians(np.array([(a, b) for a, b, *_ in rows]))
        self.xyz = np.stack([np.cos(ll[:, 0]) * np.cos(ll[:, 1]), np.cos(ll[:, 0]) * np.sin(ll[:, 1]),
                             np.sin(ll[:, 0])], 1)
        try:
            from scipy.spatial import cKDTree
            self.tree = cKDTree(self.xyz)
        except Exception:
            self.tree = None
        self.countries = {}
        with open(DATA / "countries.csv", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition(",")
                self.countries[k] = v

    def lookup(self, lat, lon):
        la, lo = math.radians(lat), math.radians(lon)
        p = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
        if self.tree is not None:
            i = int(self.tree.query(p)[1])
        else:
            i = int(np.argmin(((self.xyz - p) ** 2).sum(1)))
        _, _, name, admin1, cc = self.rows[i]
        return {"city": name, "state": admin1, "cc": cc, "country": self.countries.get(cc, cc)}

    def find(self, text):
        """Find a place by name (town, state or country) -> (lat, lon) or None."""
        t = text.strip().lower()
        if not t:
            return None
        for key in (2, 3):
            hits = [r for r in self.rows if r[key].lower() == t]
            if hits:
                return (float(np.mean([h[0] for h in hits])), float(np.mean([h[1] for h in hits])))
        cc = next((k for k, v in self.countries.items() if v.lower() == t or k.lower() == t), None)
        if cc:
            hits = [r for r in self.rows if r[4] == cc]
            if hits:
                return (float(np.median([h[0] for h in hits])), float(np.median([h[1] for h in hits])))
        return None


_PLACES = None


def places():
    global _PLACES
    if _PLACES is None:
        _PLACES = Places()
    return _PLACES


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


# ------------------------------------------------------------------ reading dates and places from files
NAME_DT = re.compile(r"(?<!\d)(20\d\d|19\d\d)[-_.]?([01]\d)[-_.]?([0-3]\d)(?:[-_. ]?([0-2]\d)[-_.]?([0-5]\d)[-_.]?([0-5]\d))?")


def _rat(x):
    try:
        return float(x)
    except Exception:
        return x[0] / x[1] if isinstance(x, tuple) and x[1] else 0.0


def _gps(g):
    try:
        lat = sum(_rat(v) / 60 ** i for i, v in enumerate(g[2]))
        lon = sum(_rat(v) / 60 ** i for i, v in enumerate(g[4]))
        if str(g.get(1, "N")).upper().startswith("S"):
            lat = -lat
        if str(g.get(3, "E")).upper().startswith("W"):
            lon = -lon
        if abs(lat) < 1e-6 and abs(lon) < 1e-6:
            return None
        return (round(lat, 5), round(lon, 5))
    except Exception:
        return None


def _parse_dt(s):
    try:
        return dt.datetime.strptime(str(s).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def photo_meta(path):
    t = gps = dh = None
    size = (0, 0)
    try:
        with Image.open(path) as im:
            size = im.size
            ex = im.getexif()
            sub = ex.get_ifd(0x8769) if ex else {}
            t = _parse_dt(sub.get(36867) or sub.get(36868) or ex.get(306)) if ex else None
            g = ex.get_ifd(0x8825) if ex else {}
            gps = _gps(g) if g else None
            dh = dhash(im)
    except Exception:
        pass
    return t, gps, dh, size


def video_meta(path):
    t = gps = None
    try:
        err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60).stderr
        m = re.search(r"location[\w.-]*\s*:\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", err)
        if m:
            gps = (round(float(m[1]), 5), round(float(m[2]), 5))
            if abs(gps[0]) < 1e-6 and abs(gps[1]) < 1e-6:
                gps = None
        m = re.search(r"creation_time\s*:\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", err)
        if m and not m[1].startswith("1970"):
            utc = dt.datetime.fromisoformat(m[1]).replace(tzinfo=dt.timezone.utc)
            t = utc.astimezone().replace(tzinfo=None)  # this computer's time zone
    except Exception:
        pass
    return t, gps


def name_time(name):
    m = NAME_DT.search(name)
    if not m:
        return None, False
    try:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if m[4]:
            return dt.datetime(y, mo, d, int(m[4]), int(m[5]), int(m[6])), True
        return dt.datetime(y, mo, d, 12, 0, 0), False
    except ValueError:
        return None, False


def dhash(im):
    try:
        g = im.convert("L").resize((9, 8), Image.BILINEAR)
        a = np.asarray(g, dtype=np.int16)
        bits = (a[:, 1:] > a[:, :-1]).flatten()
        return int("".join("1" if b else "0" for b in bits), 2)
    except Exception:
        return None


def file_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_whatsapp(path):
    s = str(path).lower()
    return "whatsapp" in s or bool(re.search(r"-wa\d{3,}", Path(path).name.lower()))


def is_screenshot(path):
    s = str(path).lower()
    return "screenshot" in s or "screen_recording" in s or "screenrecord" in s


def describe(path):
    """Everything the sorter needs to know about one file."""
    p = Path(path)
    ext = p.suffix.lower()
    kind = "photo" if ext in PHOTO_EXT else "video"
    st = p.stat()
    t, gps, dh, size = (None, None, None, (0, 0))
    if kind == "photo":
        t, gps, dh, size = photo_meta(p)
    else:
        t, gps = video_meta(p)
    nt, has_clock = name_time(p.name)
    if kind == "video" and nt and has_clock:
        t = nt  # phone video names carry local time; the file's own clock is often UTC
    if t is None and nt:
        t = nt
    src = "file"
    if t is None:
        t = dt.datetime.fromtimestamp(st.st_mtime)
        src = "modified"
    return {"kind": kind, "t": t.isoformat(timespec="seconds"), "tsrc": src, "gps": gps, "dh": dh,
            "wa": is_whatsapp(p), "shot": is_screenshot(p), "small": kind == "photo" and 0 < max(size) < 320,
            "bytes": st.st_size}


# ------------------------------------------------------------------ settings and library state
def default_settings():
    home = Path.home()
    srcs = []
    for env in ("OneDrive", "OneDriveConsumer"):
        if os.environ.get(env):
            srcs.append(Path(os.environ[env]) / "Pictures")
    srcs.append(home / "Pictures")
    seen, out = set(), []
    for s in srcs:
        k = str(s).lower()
        if k not in seen and s.exists():
            seen.add(k)
            out.append(str(s))
    return {"sources": out, "library": str(home / "Photo Albums"), "home": "", "auto": True}


def load_settings():
    s = default_settings()
    try:
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    except Exception:
        pass
    return s


def save_settings(s):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")


def _hide(path):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 2)
        except Exception:
            pass


def _write_hidden(path, data):
    path = Path(path)
    if os.name == "nt" and path.exists():
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # hidden files can't be overwritten
        except Exception:
            pass
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _hide(path)


def load_state(lib):
    try:
        return json.loads((Path(lib) / STATE_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}, "seen": {}}


def albums(lib):
    """Every album folder in the library (found by its hidden marker, so renamed folders still count)."""
    out = []
    lib = Path(lib)
    if not lib.exists():
        return out
    for mark in lib.rglob(MARK_NAME):
        try:
            info = json.loads(mark.read_text(encoding="utf-8"))
        except Exception:
            continue
        info["path"] = str(mark.parent)
        info["name"] = mark.parent.name
        out.append(info)
    out.sort(key=lambda a: a.get("start", ""), reverse=True)
    return out


def album_media(folder):
    return sorted(p for p in Path(folder).iterdir()
                  if p.is_file() and p.suffix.lower() in PHOTO_EXT | VIDEO_EXT)


# ------------------------------------------------------------------ the sort
def _scan(sources, lib):
    lib = Path(lib).resolve()
    for s in sources:
        s = Path(s)
        if not s.exists():
            continue
        if s.is_file():
            yield s
            continue
        for root, dirs, files in os.walk(s):
            r = Path(root)
            try:
                if r.resolve() == lib or lib in r.resolve().parents:
                    dirs[:] = []
                    continue
            except Exception:
                pass
            dirs[:] = [d for d in dirs if not d.startswith(".") and not any(k in d.lower() for k in SKIP_PARTS)]
            for f in files:
                if f.startswith(".") or f.startswith("~"):
                    continue
                if Path(f).suffix.lower() in PHOTO_EXT | VIDEO_EXT:
                    yield r / f


def find_home(items, override, P):
    if override:
        c = P.find(override)
        if c:
            return c, override.strip()
    days = defaultdict(set)
    pts = defaultdict(list)
    for it in items:
        if it.get("gps") and not it.get("wa"):
            pl = P.lookup(*it["gps"])
            key = (pl["cc"], pl["state"])
            days[key].add(it["t"][:10])
            pts[key].append(it["gps"])
    if len(set().union(*days.values())) < 5:
        return None, ""  # too few days to tell home from a holiday; ask for the home town instead
    key = max(days, key=lambda k: len(days[k]))
    g = np.array(pts[key])
    centre = (float(np.median(g[:, 0])), float(np.median(g[:, 1])))
    pl = P.lookup(*centre)
    return centre, f"{pl['state'] or pl['city']}, {pl['country']}"


def plan_trips(items, home, P):
    """Group away-from-home days into trips. Returns list of dicts with start, end, name, cc."""
    if not home:
        return []
    by_day = defaultdict(list)
    for it in items:
        if it.get("gps"):
            by_day[it["t"][:10]].append(it["gps"])
    away = {}
    for d, pts in by_day.items():
        far = [p for p in pts if km(p, home) > AWAY_KM]
        if len(far) >= max(1, 0.3 * len(pts)):
            away[d] = far
    trips = []
    for d in sorted(away):
        day = dt.date.fromisoformat(d)
        if trips and (day - dt.date.fromisoformat(trips[-1]["end"])).days <= 2 and not any(
                (dt.date.fromisoformat(trips[-1]["end"]) + dt.timedelta(k)).isoformat() in by_day
                and (dt.date.fromisoformat(trips[-1]["end"]) + dt.timedelta(k)).isoformat() not in away
                for k in range(1, (day - dt.date.fromisoformat(trips[-1]["end"])).days)):
            trips[-1]["end"] = d
            trips[-1]["pts"] += away[d]
        else:
            trips.append({"start": d, "end": d, "pts": list(away[d])})
    home_pl = P.lookup(*home)
    for tr in trips:
        pls = [P.lookup(*p) for p in tr.pop("pts")]
        cc = Counter(p["cc"] for p in pls).most_common(1)[0][0]
        if cc != home_pl["cc"]:
            name = P.countries.get(cc, cc)
        else:
            state = Counter(p["state"] for p in pls if p["cc"] == cc).most_common(1)[0][0]
            name = state if state and state != home_pl["state"] else \
                Counter(p["city"] for p in pls).most_common(1)[0][0]
        tr["name"], tr["cc"] = name, cc
    return trips


def trip_folder_name(tr):
    a, b = dt.date.fromisoformat(tr["start"]), dt.date.fromisoformat(tr["end"])
    if a == b:
        when = a.strftime("%d.%m.%Y")
    elif (a.year, a.month) == (b.year, b.month):
        when = f"{a:%d}-{b:%d.%m.%Y}"
    elif a.year == b.year:
        when = f"{a:%d.%m}-{b:%d.%m.%Y}"
    else:
        when = f"{a:%d.%m.%Y}-{b:%d.%m.%Y}"
    return f"{tr['name']} Trip {when}"


def _safe(name):
    return re.sub(r'[<>:"/\\|?*]+', " ", name).strip() or "Album"


def sort_photos(sources, library, home_text="", progress=None, log=print, extra_files=(), album_name=""):
    """Copy new photos and videos from `sources` into tidy album folders under `library`."""
    if not LOCK.acquire(blocking=False):
        return {"busy": True, "message": "Already sorting, please wait a moment."}
    try:
        return _sort(sources, library, home_text, progress, log, extra_files, album_name)
    finally:
        LOCK.release()


def _sort(sources, library, home_text, progress, log, extra_files, album_name=""):
    P = places()
    lib = Path(library)
    lib.mkdir(parents=True, exist_ok=True)
    state = load_state(lib)
    files, seen = state.setdefault("files", {}), state.setdefault("seen", {})
    skipped = set(state.get("skipped", []))
    prog = progress or (lambda f, m: None)

    prog(0.02, "Looking for photos and videos...")
    found = list(_scan(sources, lib)) + [Path(f) for f in extra_files]
    new = []
    for i, p in enumerate(found):
        if i % 50 == 0:
            prog(0.05 + 0.55 * i / max(1, len(found)), f"Reading {i + 1} of {len(found)}...")
        try:
            st = p.stat()
        except OSError:
            continue
        key = str(p)
        sig = [st.st_size, int(st.st_mtime)]
        old = seen.get(key)
        if old and old[:2] == sig and (old[2] in files or old[2] in skipped):
            continue
        try:
            h = file_hash(p)
        except OSError:
            continue
        seen[key] = sig + [h]
        if h in files or h in skipped or any(h == n[1] for n in new):
            continue
        try:
            info = describe(p)
        except Exception as e:
            log(f"(skipped {p.name}: {e})")
            continue
        if info.pop("small"):
            skipped.add(h)
            continue
        new.append((p, h, info))

    # near-duplicates: WhatsApp copies of photos you already have
    known = np.array([f["dh"] for f in files.values() if f.get("dh") is not None] +
                     [i["dh"] for _, _, i in new if i.get("dh") is not None and not i["wa"]], dtype=np.uint64)
    kept, dupes = [], 0
    for p, h, info in new:
        if info["wa"] and info.get("dh") is not None and len(known):
            dist = np.unpackbits((known ^ np.uint64(info["dh"])).view(np.uint8).reshape(-1, 8), axis=1).sum(1)
            if dist.min() <= 4:
                dupes += 1
                skipped.add(h)
                continue
        kept.append((p, h, info))

    everything = [dict(f) for f in files.values()] + [i for _, _, i in kept]
    home, home_name = find_home(everything, home_text, P)
    trips = plan_trips(everything, home, P)
    existing = albums(lib)

    def album_for_trip(tr):
        for a in existing:
            if a.get("kind") == "trip" and a["start"] <= tr["end"] and tr["start"] <= a["end"]:
                if tr["start"] < a["start"] or tr["end"] > a["end"]:
                    a["start"], a["end"] = min(a["start"], tr["start"]), max(a["end"], tr["end"])
                    _write_hidden(Path(a["path"]) / MARK_NAME, {k: a[k] for k in ("kind", "start", "end", "place")})
                return Path(a["path"])
        folder = lib / tr["start"][:4] / _safe(trip_folder_name(tr))
        folder.mkdir(parents=True, exist_ok=True)
        rec = {"kind": "trip", "start": tr["start"], "end": tr["end"], "place": tr["name"]}
        _write_hidden(folder / MARK_NAME, rec)
        existing.append(dict(rec, path=str(folder), name=folder.name))
        return folder

    def album_for_month(month, kind="month"):
        for a in existing:
            if a.get("kind") == kind and a.get("start") == month:
                return Path(a["path"])
        y, m = int(month[:4]), int(month[5:7])
        if kind == "month":
            folder = lib / month[:4] / f"{month} {MONTHS[m - 1]}"
        else:
            folder = lib / "Screenshots" / f"{month} {MONTHS[m - 1]}"
        folder.mkdir(parents=True, exist_ok=True)
        rec = {"kind": kind, "start": month, "end": month, "place": ""}
        _write_hidden(folder / MARK_NAME, rec)
        existing.append(dict(rec, path=str(folder), name=folder.name))
        return folder

    def album_named(name, days):
        for a in existing:
            if a.get("kind") == "named" and a.get("place") == name:
                if days[0] < a["start"] or days[-1] > a["end"]:
                    a["start"], a["end"] = min(a["start"], days[0]), max(a["end"], days[-1])
                    _write_hidden(Path(a["path"]) / MARK_NAME, {k: a[k] for k in ("kind", "start", "end", "place")})
                return Path(a["path"])
        folder = lib / days[0][:4] / _safe(name)
        folder.mkdir(parents=True, exist_ok=True)
        rec = {"kind": "named", "start": days[0], "end": days[-1], "place": name}
        _write_hidden(folder / MARK_NAME, rec)
        existing.append(dict(rec, path=str(folder), name=folder.name))
        return folder

    extra_set = {str(Path(f)) for f in extra_files}
    album_name = (album_name or "").strip()
    named_days = sorted(i["t"][:10] for p, _, i in kept if str(p) in extra_set)
    counts = Counter()
    for i, (p, h, info) in enumerate(kept):
        if i % 20 == 0:
            prog(0.62 + 0.36 * i / max(1, len(kept)), f"Copying {i + 1} of {len(kept)}...")
        day = info["t"][:10]
        if album_name and str(p) in extra_set:
            folder = album_named(album_name, named_days)
        elif info["shot"]:
            folder = album_for_month(day[:7], "screenshots")
        else:
            tr = next((t for t in trips if t["start"] <= day <= t["end"]), None)
            folder = album_for_trip(tr) if tr else album_for_month(day[:7])
        dst = folder / p.name
        k = 1
        while dst.exists():
            dst = folder / f"{p.stem}_{k}{p.suffix}"
            k += 1
        try:
            shutil.copy2(p, dst)
        except OSError as e:
            log(f"(could not copy {p.name}: {e})")
            continue
        files[h] = dict(info, dest=str(dst.relative_to(lib)))
        counts[str(folder.relative_to(lib))] += 1

    state["skipped"] = sorted(skipped)
    _write_hidden(lib / STATE_NAME, state)
    prog(1.0, "Done")
    return {"new": sum(counts.values()), "dupes": dupes, "folders": dict(counts), "home": home_name,
            "trips": [trip_folder_name(t) for t in trips], "library": str(lib), "checked": len(found)}


def summary_text(r):
    if r.get("busy"):
        return r["message"]
    if not r["checked"]:
        return "No photos or videos found in those folders yet."
    lines = [f"**Sorted {r['new']} new photos and videos** into `{r['library']}`."]
    if r["folders"]:
        lines += ["\n".join(f"- {name}: {n}" for name, n in sorted(r["folders"].items()))]
    else:
        lines.append("Nothing new: everything was already sorted.")
    if r["dupes"]:
        lines.append(f"Skipped {r['dupes']} WhatsApp copies of photos you already have.")
    if r["home"]:
        lines.append(f"Home looks like **{r['home']}** (photos taken more than {AWAY_KM} km away count as a trip).")
    else:
        lines.append("No photo had a location yet, so everything went into month folders. "
                     "Type your home town below so trips can be found.")
    return "\n\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Sort photos and videos into trip and month folders.")
    ap.add_argument("--source", action="append", help="folder to read from (repeat for more)")
    ap.add_argument("--library", help="where the tidy album folders go")
    ap.add_argument("--home", default="", help="your home town, state or country (default: worked out)")
    a = ap.parse_args()
    s = load_settings()
    r = sort_photos(a.source or s["sources"], a.library or s["library"], a.home or s.get("home", ""),
                    progress=lambda f, m: print(f"{int(f * 100):3d}% {m}"))
    print(summary_text(r))


if __name__ == "__main__":
    main()

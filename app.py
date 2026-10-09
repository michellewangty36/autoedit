"""
AutoEdit web app.
  Tab 1 "Make it for me": upload photos & videos, pick a style, press the button.
  Tab 2 "Edit it myself": change the shots, order, lengths, speed, transitions, text and subtitles,
                          add more clips, then re-make the video.
Start it with the START file for your computer, or:  python app.py
It opens in your web browser at http://127.0.0.1:7860 (runs only on your computer).
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # fully offline, no tracking
import gradio as gr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoedit  # noqa: E402

OUT = HERE / "output"
PROJECTS = OUT / "projects"
STYLE_CHOICES = [("Auto (let it decide)", "auto")] + [
    (f"{k.capitalize()}: {v['desc']}", k) for k, v in autoedit.STYLES.items()]
STYLE_ONLY = [(f"{k.capitalize()}", k) for k in autoedit.STYLES]
ASPECT_CHOICES = [("Default for style", "default"), ("16:9 YouTube / TV", "16:9"),
                  ("9:16 TikTok / Reels / Shorts", "9:16"), ("1:1 Square", "1:1"), ("4:5 Instagram post", "4:5")]
MOOD_CHOICES = [("Match the style", "default")] + [(m.capitalize(), m) for m in autoedit.music_gen.MOODS]
LANG_CHOICES = [("Auto-detect", ""), ("English", "en"), ("Chinese 中文", "zh"), ("Malay", "ms"),
                ("Cantonese 粵語", "yue"), ("Japanese 日本語", "ja"), ("Korean 한국어", "ko"),
                ("Indonesian", "id"), ("Thai", "th"), ("Vietnamese", "vi"), ("Tamil", "ta"),
                ("Hindi", "hi"), ("Spanish", "es"), ("French", "fr")]
QUALITY = {"720p (faster)": 720, "1080p Full HD": 1080, "4K (slow)": 2160}
SHOT_HEADERS = ["ID", "Order", "Keep", "File", "Type", "Start at (s)", "Length (s)", "Speed",
                "Transition in", "Effect", "Text on screen", "AI saw"]
SUB_HEADERS = ["Shot ID", "From (s into shot)", "To (s into shot)", "Subtitle"]
TRANSITIONS_HELP = ("**Transition in** can be `auto` or one of: " +
                    ", ".join(f"`{t}`" for t in autoedit.XFADE_TRANSITIONS) +
                    ".\n\n**Effect** can be: " + ", ".join(f"`{e}`" for e in autoedit.EFFECTS) +
                    ".\n\n**Speed**: 1 = normal, 0.5 = slow motion, 2 = fast.")


def new_project_dir():
    PROJECTS.mkdir(parents=True, exist_ok=True)
    olds = sorted(p for p in PROJECTS.iterdir() if p.is_dir())
    for p in olds[:-15]:  # keep the 15 most recent projects' media
        shutil.rmtree(p, ignore_errors=True)
    d = PROJECTS / dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    (d / "media").mkdir(parents=True)
    return d


def copy_in(files, folder):
    out = []
    for f in files or []:
        src = Path(f if isinstance(f, str) else f.name)
        dst = folder / src.name
        k = 1
        while dst.exists():
            dst = folder / f"{src.stem}_{k}{src.suffix}"
            k += 1
        shutil.copy(src, dst)
        out.append(dst)
    return out


def file_path(f):
    if not f:
        return None
    return f if isinstance(f, str) else getattr(f, "name", None)


# ------------------------------------------------------------------ plan <-> tables
def thumbs_for(plan):
    """Small picture of every shot, for the gallery."""
    tdir = Path(plan["_dir"]) / "thumbs"
    tdir.mkdir(exist_ok=True)
    items = []
    for i, sh in enumerate(plan["shots"]):
        t = tdir / f"{i}_{int(float(sh['start']) * 10)}.jpg"
        if not t.exists():
            try:
                if sh["kind"] == "photo":
                    from PIL import Image, ImageOps
                    with Image.open(sh["path"]) as im:
                        im = ImageOps.exif_transpose(im).convert("RGB")
                        im.thumbnail((320, 320))
                        im.save(t, quality=85)
                else:
                    subprocess.run([autoedit.FFMPEG, "-y", "-v", "error", "-ss", f"{float(sh['start']) + 0.3:.2f}",
                                    "-i", sh["path"], "-frames:v", "1", "-vf", "scale=320:-2", str(t)],
                                   capture_output=True)
            except Exception:
                pass
        if t.exists():
            mark = "" if sh.get("keep", True) else " (removed)"
            items.append((str(t), f"ID {i} · {Path(sh['path']).name}{mark}"))
    return items


def plan_to_tables(plan):
    rows, subs = [], []
    for i, sh in enumerate(plan["shots"]):
        rows.append([i, sh.get("order", i + 1), bool(sh.get("keep", True)), Path(sh["path"]).name, sh["kind"],
                     round(float(sh.get("start", 0)), 2), round(float(sh.get("dur", 3)), 2),
                     float(sh.get("speed", 1.0)), sh.get("transition", "auto"), sh.get("effect", "none"),
                     sh.get("text", ""),
                     sh.get("label", "")])
        for l in sh.get("subs") or []:
            subs.append([i, round(float(l["start"]) - float(sh.get("start", 0)), 2),
                         round(float(l["end"]) - float(sh.get("start", 0)), 2), l["text"]])
    return rows, subs


def _rows(table):
    if table is None:
        return []
    if hasattr(table, "values"):  # pandas DataFrame
        return table.values.tolist()
    if isinstance(table, dict) and "data" in table:
        return table["data"]
    return list(table)


def _num(v, default):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "✓")
    return bool(v)


def tables_to_plan(plan, shot_table, sub_table):
    shots = plan["shots"]
    old_start = [float(sh.get("start", 0)) for sh in shots]
    # subtitles first (their times are "seconds into the shot" as it was shown)
    new_subs = {i: [] for i in range(len(shots))}
    for r in _rows(sub_table):
        if len(r) < 4:
            continue
        i = int(_num(r[0], -1))
        if i not in new_subs or not str(r[3] or "").strip():
            continue
        a, b = _num(r[1], 0), _num(r[2], 0)
        if b <= a:
            b = a + 1.5
        new_subs[i].append(dict(start=old_start[i] + a, end=old_start[i] + b, text=str(r[3]).strip()))
    for i, sh in enumerate(shots):
        if sh["kind"] == "video":
            sh["subs"] = sorted(new_subs[i], key=lambda x: x["start"])
    for r in _rows(shot_table):
        if len(r) < 11:
            continue
        i = int(_num(r[0], -1))
        if not 0 <= i < len(shots):
            continue
        sh = shots[i]
        sh["order"] = _num(r[1], sh.get("order", i + 1))
        sh["keep"] = _bool(r[2])
        sh["start"] = max(0.0, _num(r[5], sh.get("start", 0))) if sh["kind"] == "video" else 0.0
        sh["dur"] = max(0.8, _num(r[6], sh.get("dur", 3)))
        sh["speed"] = min(4.0, max(0.25, _num(r[7], 1.0)))
        t = str(r[8] or "auto").strip().lower()
        sh["transition"] = t if t in autoedit.XFADE_TRANSITIONS else "auto"
        e = str(r[9] or "none").strip().lower()
        sh["effect"] = e if e in autoedit.EFFECTS else "none"
        sh["text"] = str(r[10] or "")
    return plan


def shot_from_file(path, order):
    path = Path(path)
    ext = path.suffix.lower()
    if ext in autoedit.PHOTO_EXT:
        meta = autoedit.analyse_photo(path)
        return dict(order=order, keep=True, kind="photo", path=str(path), start=0.0, dur=3.0, speed=1.0,
                    transition="auto", effect="none", text="", label="", speech=False, shaky=False,
                    face_x=meta["face_x"], face_y=meta["face_y"])
    if ext in autoedit.VIDEO_EXT:
        info = autoedit.probe(path)
        if not info["video"]:
            return None
        return dict(order=order, keep=True, kind="video", path=str(path), start=0.0,
                    dur=round(min(info["duration"], 8.0), 2), speed=1.0, transition="auto", effect="none", text="",
                    label="",
                    speech=False, shaky=False, face_x=None,
                    info=dict(w=info["w"], h=info["h"], audio=info["audio"], hdr=info["hdr"],
                              duration=info["duration"]))
    return None


def save_plan(plan):
    p = Path(plan["_dir"]) / "project.autoedit.json"
    p.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    return str(p)


def edit_outputs(plan, msg=""):
    rows, subs = plan_to_tables(plan)
    return (plan, rows, subs, thumbs_for(plan), plan.get("style"), plan.get("title", ""),
            int(round(float(plan.get("music_volume", 0.4)) * 100)), plan.get("mood") or "default", msg)


# ------------------------------------------------------------------ actions
def make(files, style, focus, smart, aspect, length, title, subtitle_text, music_file, mood, subs, lang, translate,
         quality, music_volume, progress=gr.Progress()):
    if not files:
        raise gr.Error("Please add some photos or videos first.")
    pdir = new_project_dir()
    copy_in(files, pdir / "media")
    music = copy_in([file_path(music_file)], pdir) if music_file else []
    log_lines = []
    try:
        summary = autoedit.make_video(
            [str(pdir / "media")], style=style, aspect=None if aspect == "default" else aspect,
            length=length or None, title=title or None, subtitle_text=subtitle_text or None,
            music=str(music[0]) if music else None,
            mood=None if mood == "default" else mood, subtitles=subs, sub_language=lang or None,
            translate=translate, resolution=QUALITY[quality], music_volume=music_volume / 100.0,
            progress=lambda frac, msg: progress(frac, desc=msg), log=log_lines.append,
            smart=smart, focus=focus or None)
    except Exception as e:
        raise gr.Error(f"Something went wrong: {e}")
    plan = autoedit.load_plan(summary["plan"])
    plan["_dir"] = str(pdir)
    save_plan(plan)
    info = (f"**Done!** {summary['duration']}s · {summary['shots']} shots · style *{summary['style']}* · "
            f"music: {summary['music']} · {summary['subtitles']} subtitle lines"
            + (f" ({summary['language']})" if summary.get("language") else "")
            + f"\n\nSaved to `{summary['output']}`\n\nWant to change something? Open the **✂️ Edit it myself** tab.")
    downloads = [summary["output"]] + ([summary["srt"]] if summary["srt"] else [])
    return (summary["output"], downloads, info, "\n".join(log_lines)) + edit_outputs(plan, "Loaded your video. Change anything below, then press **Re-make video**.")


def remake(plan, shot_table, sub_table, style, title, music_volume, mood, music_file, progress=gr.Progress()):
    if not plan:
        raise gr.Error("Make a video first (tab 1), add clips below, or open a saved project.")
    if shot_table is not None:
        plan = tables_to_plan(plan, shot_table, sub_table)
    plan["style"] = style or plan["style"]
    plan["title"] = title or ""
    plan["music_volume"] = music_volume / 100.0
    if mood and mood != "default":
        plan["mood"] = mood
    if music_file:
        plan["music"] = str(copy_in([file_path(music_file)], Path(plan["_dir"]))[0])
    elif mood and mood != "default":
        plan["music"] = None  # a new mood means a newly composed track
    save_plan(plan)
    log_lines = []
    try:
        summary = autoedit.render_plan(plan, progress=lambda frac, msg: progress(frac, desc=msg),
                                       log=log_lines.append)
    except Exception as e:
        raise gr.Error(f"Something went wrong: {e}")
    downloads = [summary["output"]] + ([summary["srt"]] if summary["srt"] else [])
    msg = (f"**Re-made!** {summary['duration']}s · {summary['shots']} shots. Saved to `{summary['output']}`. "
           "Keep editing and press Re-make again any time.")
    return (summary["output"], downloads) + edit_outputs(plan, msg)


def lively(plan, shot_table, sub_table, style, title, music_volume, mood, music_file, progress=gr.Progress()):
    if not plan:
        raise gr.Error("Make a video first, or add some clips.")
    plan = autoedit.make_lively(tables_to_plan(plan, shot_table, sub_table))
    return remake(plan, None, None, style, title, int(plan["music_volume"] * 100),
                  plan.get("mood") or mood, music_file, progress)


def add_clips(plan, shot_table, sub_table, files):
    if not files:
        raise gr.Error("Choose the photos or videos to add first.")
    if not plan:
        pdir = new_project_dir()
        plan = dict(version=1, style="vlog", aspect=None, resolution=1080, mood=None, music=None, music_volume=0.4,
                    title="", subtitle_text="", subtitles=True, language=None, seed=1, shots=[], _dir=str(pdir))
    else:
        plan = tables_to_plan(plan, shot_table, sub_table)
    media = Path(plan["_dir"]) / "media"
    media.mkdir(exist_ok=True)
    order = max([float(s.get("order", 0)) for s in plan["shots"]] + [0])
    added = 0
    for p in copy_in(files, media):
        sh = shot_from_file(p, order + 1)
        if sh:
            order += 1
            plan["shots"].append(sh)
            added += 1
    save_plan(plan)
    return edit_outputs(plan, f"Added {added} clip(s) at the end. Change their **Order** to move them.")


def open_project(f):
    path = file_path(f)
    if not path:
        raise gr.Error("Choose a .autoedit.json project file.")
    plan = autoedit.load_plan(path)
    if not plan.get("_dir") or not Path(plan["_dir"]).exists():
        pdir = new_project_dir()
        plan["_dir"] = str(pdir)
    missing = [s for s in plan["shots"] if not Path(s["path"]).exists()]
    save_plan(plan)
    msg = "Project opened." + (f" {len(missing)} clip(s) can't be found on this computer and will be skipped."
                               if missing else "")
    return edit_outputs(plan, msg)


# ------------------------------------------------------------------ look & feel
THEME = gr.themes.Soft(primary_hue="fuchsia", secondary_hue="cyan", neutral_hue="slate", radius_size="lg",
                       font=["Poppins", "Segoe UI", "system-ui", "sans-serif"])

CSS = """
html, body, gradio-app, .gradio-container, .main, .app {
  background: linear-gradient(-45deg, #ff6ec4, #7873f5, #4ade80, #22d3ee, #fbbf24, #f472b6) !important;
  background-size: 500% 500% !important;
  animation: aeBg 22s ease infinite;
}
@keyframes aeBg { 0% {background-position: 0% 50%} 50% {background-position: 100% 50%} 100% {background-position: 0% 50%} }
.gradio-container { max-width: 1280px !important; }
.block, .form, .tabitem, .tab-wrapper + div {
  border-radius: 20px !important;
  background: rgba(255,255,255,0.86) !important;
  backdrop-filter: blur(10px);
  box-shadow: 0 8px 30px rgba(80, 20, 120, 0.18) !important;
  border: none !important;
}
.dark .block, .dark .form, .dark .tabitem { background: rgba(24, 16, 40, 0.82) !important; }
#ae-hero { text-align: center; padding: 18px 10px 6px; background: transparent !important; box-shadow: none !important; }
#ae-hero h1 {
  font-size: clamp(2.2rem, 5vw, 3.6rem); font-weight: 900; margin: 0; letter-spacing: -1px;
  color: #fff; text-shadow: 0 4px 0 rgba(120, 30, 160, .35), 0 10px 30px rgba(0,0,0,.25);
}
#ae-hero p { color: #fff; font-size: 1.1rem; margin: 6px 0 0; text-shadow: 0 2px 8px rgba(0,0,0,.25); }
.ae-emojis span { display: inline-block; font-size: 2rem; margin: 6px 8px 0; animation: aeFloat 2.6s ease-in-out infinite; }
.ae-emojis span:nth-child(2n) { animation-delay: .4s } .ae-emojis span:nth-child(3n) { animation-delay: .8s }
@keyframes aeFloat { 0%,100% { transform: translateY(0) rotate(0) } 50% { transform: translateY(-10px) rotate(8deg) } }
button.primary, .ae-go {
  background: linear-gradient(90deg, #ff4fd8, #ff8a3d, #ffd23f) !important; color: #fff !important;
  font-weight: 800 !important; font-size: 1.15rem !important; border: none !important;
  box-shadow: 0 8px 24px rgba(255, 79, 216, .45) !important; transition: transform .15s ease, box-shadow .15s ease;
}
button.primary:hover, .ae-go:hover { transform: translateY(-2px) scale(1.03); box-shadow: 0 12px 32px rgba(255, 79, 216, .6) !important; }
button.secondary {
  background: linear-gradient(90deg, #22d3ee, #818cf8) !important; color: #fff !important; font-weight: 700 !important;
  border: none !important;
}
button.secondary:hover { transform: translateY(-2px); }
button.ae-lively, .ae-lively button { background: linear-gradient(90deg, #a855f7, #ec4899, #f97316) !important; color: #fff !important;
  font-weight: 800 !important; animation: aePulse 2.4s ease-in-out infinite; border: none !important; }
button.ae-lively:hover { transform: translateY(-2px) scale(1.03); }
@keyframes aePulse { 0%,100% { box-shadow: 0 0 0 0 rgba(236,72,153,.55) } 50% { box-shadow: 0 0 0 12px rgba(236,72,153,0) } }
.tab-wrapper button, button[role="tab"] { font-weight: 800 !important; font-size: 1.05rem !important; }
button[role="tab"][aria-selected="true"] { color: #c026d3 !important; }
"""

HERO = """
<div id="ae-hero-inner">
  <h1>🎬 AutoEdit</h1>
  <p>Drop in your photos & videos and get a fun, ready-to-post video, then change anything you like.</p>
  <div class="ae-emojis"><span>📸</span><span>🎵</span><span>✨</span><span>🌈</span><span>🎉</span><span>🎞️</span></div>
</div>
"""


# ------------------------------------------------------------------ page
def _df(headers, types, label):
    kw = dict(headers=headers, datatype=types, interactive=True, label=label, wrap=True,
              col_count=(len(headers), "fixed"), type="array")
    try:
        return gr.Dataframe(**kw, static_columns=[0, 3, 4, 11] if len(headers) > 4 else [0])
    except TypeError:
        return gr.Dataframe(**kw)


with gr.Blocks(title="AutoEdit - free automatic video editor", analytics_enabled=False) as demo:
    gr.HTML(HERO, elem_id="ae-hero")
    plan_state = gr.State(None)
    with gr.Tabs():
        with gr.Tab("🎬 Make it for me"):
            with gr.Row():
                with gr.Column(scale=1):
                    files = gr.File(label="1. Your photos & videos", file_count="multiple", type="filepath")
                    style = gr.Dropdown(STYLE_CHOICES, value="auto", label="2. Style")
                    focus = gr.Textbox(label="What should it focus on? (optional)",
                                       placeholder="e.g. food, beach, sunset, my dog")
                    smart = gr.Checkbox(value=True, label="🧠 Smart AI: understands what's in your shots (free, offline)")
                    title = gr.Textbox(label="Title (optional)", placeholder="e.g. Bali Trip 2026")
                    subtitle_text = gr.Textbox(label="Small line under the title (optional, default: the date)")
                    with gr.Accordion("More options", open=False):
                        aspect = gr.Dropdown(ASPECT_CHOICES, value="default", label="Shape")
                        length = gr.Slider(0, 600, value=0, step=5, label="Length in seconds (0 = automatic)")
                        subs = gr.Checkbox(value=True, label="Auto subtitles")
                        lang = gr.Dropdown(LANG_CHOICES, value="", label="Spoken language")
                        translate = gr.Checkbox(value=False, label="Translate subtitles to English")
                        music_file = gr.File(label="Your own song (optional, mp3/wav/m4a)", type="filepath",
                                             file_types=["audio"])
                        mood = gr.Dropdown(MOOD_CHOICES, value="default", label="Music mood (if no song uploaded)")
                        music_volume = gr.Slider(0, 100, value=40, step=5, label="Music volume")
                        quality = gr.Radio(list(QUALITY), value="1080p Full HD", label="Quality")
                    go = gr.Button("🎬 Make my video", variant="primary", size="lg")
                with gr.Column(scale=1):
                    video = gr.Video(label="Your video")
                    info = gr.Markdown()
                    downloads = gr.File(label="Download (video + subtitle file)", file_count="multiple")
                    with gr.Accordion("What it did", open=False):
                        details = gr.Textbox(lines=14, show_label=False)

        with gr.Tab("✂️ Edit it myself"):
            edit_msg = gr.Markdown("Make a video in the first tab, or add your own clips below to start from "
                                   "scratch, or open a saved project.")
            gallery = gr.Gallery(label="Your shots (ID number matches the table)", columns=6, height=260,
                                 allow_preview=True)
            shot_table = _df(SHOT_HEADERS, ["number", "number", "bool", "str", "str", "number", "number", "number",
                                            "str", "str", "str", "str"],
                             "Shots: untick Keep to remove, change Order to move, edit Start / Length / Speed / Text")
            gr.Markdown(TRANSITIONS_HELP)
            sub_table = _df(SUB_HEADERS, ["number", "number", "number", "str"],
                            "Subtitles: fix the words or timing; delete the text to remove a line")
            with gr.Row():
                e_style = gr.Dropdown(STYLE_ONLY, label="Style / look")
                e_title = gr.Textbox(label="Title (empty = no title)")
                e_mood = gr.Dropdown(MOOD_CHOICES, value="default", label="Music mood")
                e_volume = gr.Slider(0, 100, value=40, step=5, label="Music volume")
            with gr.Row():
                e_music = gr.File(label="Change the song (optional)", type="filepath", file_types=["audio"])
                add_files = gr.File(label="Add more photos / videos", file_count="multiple", type="filepath")
                open_file = gr.File(label="Open a saved project (.autoedit.json)", type="filepath")
            with gr.Row():
                add_btn = gr.Button("➕ Add these clips", variant="secondary")
                open_btn = gr.Button("📂 Open project", variant="secondary")
                lively_btn = gr.Button("✨ Make it more lively", size="lg", elem_classes=["ae-lively"])
                remake_btn = gr.Button("🎬 Re-make video with my changes", variant="primary", size="lg")
            e_video = gr.Video(label="Your edited video")
            e_downloads = gr.File(label="Download", file_count="multiple")

    edit_outs = [plan_state, shot_table, sub_table, gallery, e_style, e_title, e_volume, e_mood, edit_msg]
    go.click(make, [files, style, focus, smart, aspect, length, title, subtitle_text, music_file, mood, subs, lang,
                    translate, quality, music_volume], [video, downloads, info, details] + edit_outs)
    remake_btn.click(remake, [plan_state, shot_table, sub_table, e_style, e_title, e_volume, e_mood, e_music],
                     [e_video, e_downloads] + edit_outs)
    lively_btn.click(lively, [plan_state, shot_table, sub_table, e_style, e_title, e_volume, e_mood, e_music],
                     [e_video, e_downloads] + edit_outs)
    add_btn.click(add_clips, [plan_state, shot_table, sub_table, add_files], edit_outs)
    open_btn.click(open_project, [open_file], edit_outs)

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    demo.queue().launch(inbrowser=True, allowed_paths=[str(OUT)], theme=THEME, css=CSS)

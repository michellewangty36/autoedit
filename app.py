"""
AutoEdit web app: upload photos & videos, pick a style, press the button.
Start it with the START file for your computer, or:  python app.py
It opens in your web browser at http://127.0.0.1:7860 (runs only on your computer).
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # fully offline, no tracking
import gradio as gr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoedit  # noqa: E402

STYLE_CHOICES = [("Auto (let it decide)", "auto")] + [
    (f"{k.capitalize()}: {v['desc']}", k) for k, v in autoedit.STYLES.items()]
ASPECT_CHOICES = [("Default for style", "default"), ("16:9 YouTube / TV", "16:9"),
                  ("9:16 TikTok / Reels / Shorts", "9:16"), ("1:1 Square", "1:1"), ("4:5 Instagram post", "4:5")]
MOOD_CHOICES = [("Match the style", "default")] + [(m.capitalize(), m) for m in autoedit.music_gen.MOODS]
LANG_CHOICES = [("Auto-detect", ""), ("English", "en"), ("Chinese 中文", "zh"), ("Malay", "ms"),
                ("Cantonese 粵語", "yue"), ("Japanese 日本語", "ja"), ("Korean 한국어", "ko"),
                ("Indonesian", "id"), ("Thai", "th"), ("Vietnamese", "vi"), ("Tamil", "ta"),
                ("Hindi", "hi"), ("Spanish", "es"), ("French", "fr")]


def make(files, style, aspect, length, title, subtitle_text, music_file, mood, subs, lang, translate,
         quality, music_volume, progress=gr.Progress()):
    if not files:
        raise gr.Error("Please add some photos or videos first.")
    job = Path(tempfile.mkdtemp(prefix="upload_", dir=HERE / "output"))
    for f in files:
        src = Path(f if isinstance(f, str) else f.name)
        shutil.copy(src, job / src.name)
    log_lines = []

    def log(msg):
        log_lines.append(msg)

    try:
        res = {"720p (faster)": 720, "1080p Full HD": 1080, "4K (slow)": 2160}[quality]
        summary = autoedit.make_video(
            [str(job)], style=style, aspect=None if aspect == "default" else aspect,
            length=length or None, title=title or None, subtitle_text=subtitle_text or None,
            music=(music_file if isinstance(music_file, str) else getattr(music_file, "name", None)) if music_file else None,
            mood=None if mood == "default" else mood, subtitles=subs, sub_language=lang or None,
            translate=translate, resolution=res, music_volume=music_volume / 100.0,
            progress=lambda frac, msg: progress(frac, desc=msg), log=log)
    except Exception as e:
        raise gr.Error(f"Something went wrong: {e}")
    finally:
        shutil.rmtree(job, ignore_errors=True)
    info = (f"**Done!** {summary['duration']}s · {summary['shots']} shots · style *{summary['style']}* · "
            f"music: {summary['music']} · {summary['subtitles']} subtitle lines"
            + (f" ({summary['language']})" if summary.get("language") else "")
            + f"\n\nSaved to `{summary['output']}`")
    downloads = [summary["output"]] + ([summary["srt"]] if summary["srt"] else [])
    return summary["output"], downloads, info, "\n".join(log_lines)


with gr.Blocks(title="AutoEdit - free automatic video editor", analytics_enabled=False) as demo:
    gr.Markdown("# 🎬 AutoEdit\nUpload your photos and videos, choose a style, press **Make my video**. "
                "Everything runs on your own computer, free.")
    with gr.Row():
        with gr.Column(scale=1):
            files = gr.File(label="1. Your photos & videos", file_count="multiple", type="filepath")
            style = gr.Dropdown(STYLE_CHOICES, value="auto", label="2. Style")
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
                quality = gr.Radio(["720p (faster)", "1080p Full HD", "4K (slow)"], value="1080p Full HD",
                                   label="Quality")
            go = gr.Button("🎬 Make my video", variant="primary", size="lg")
        with gr.Column(scale=1):
            video = gr.Video(label="Your video")
            info = gr.Markdown()
            downloads = gr.File(label="Download (video + subtitle file)", file_count="multiple")
            with gr.Accordion("What it did", open=False):
                details = gr.Textbox(lines=14, show_label=False)
    go.click(make, [files, style, aspect, length, title, subtitle_text, music_file, mood, subs, lang, translate,
                    quality, music_volume], [video, downloads, info, details])

if __name__ == "__main__":
    (HERE / "output").mkdir(exist_ok=True)
    demo.queue().launch(inbrowser=True, allowed_paths=[str(HERE / "output")])

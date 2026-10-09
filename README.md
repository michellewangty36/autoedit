# AutoEdit: free automatic video editor

Drop in your photos and videos, choose a style, and press one button. AutoEdit edits them into a finished vlog, travel video, cinematic film or TikTok/Reels clip. It runs on your own computer. It is free, needs no account, and adds no watermark.

## How to start it

1. **Download `AutoEdit.zip`**, right-click it and choose **Extract All**. Your Desktop is a good place for it.
2. **Windows:** double-click **`START (Windows).bat`**.
   **Mac:** double-click **`START (Mac).command`**. If the Mac says it can't open it, right-click the file, choose **Open**, then **Open** again.
3. The first start sets everything up, which takes a few minutes and happens only once. On Windows it installs Python by itself if needed, and this also works on ARM laptops. On a Mac, if Python is missing, it opens the download page; install Python, then double-click START again.
4. Your web browser opens AutoEdit. Then:
   - **Add your photos and videos.** You can select many at once.
   - **Pick a style.** Leave it on *Auto* if you're not sure.
   - Type a **title** if you want one, for example *Bali Trip 2026*.
   - Press **🎬 Make my video**.
5. Watch the result in the browser and use the **Download** button. You also get a subtitle file (`.srt`). Every video is saved in the `output` folder as well.

Keep the black window open while you use AutoEdit. Closing it stops the app.

## What it does automatically

| | |
|---|---|
| ✂️ **Smart cutting** | It finds scene changes and keeps the sharpest and most interesting moments. It drops blurry, dark, over-exposed and shaky parts, and the fumbling at the start and end of each clip. |
| 🗣️ **Keeps people talking** | In vlog styles it keeps full sentences together and cuts out the long pauses between them. |
| 📝 **Auto subtitles** | It writes subtitles for any language (English, 中文, Malay, Cantonese, Japanese, Korean and more). It can also translate them into English. The subtitles are burned into the video and also saved as an `.srt` file. |
| 🎵 **Music** | It uses your own songs from the `music` folder. If that folder is empty, it composes a **brand-new royalty-free track** for every video, matched to the length and mood, so it is safe to post anywhere. |
| 🥁 **Beat-synced cuts** | Cuts land on the beat of the music. The music gets quieter automatically when someone speaks. |
| 🖼️ **Photos come alive** | Photos get smooth zoom and pan ("Ken Burns") that focuses on faces. Near-duplicate photos (bursts) are removed, and the sharpest one is kept. |
| 📐 **Any shape** | It makes 16:9 (YouTube), 9:16 (TikTok/Reels/Shorts), 1:1 or 4:5 videos. Vertical clips get a blurred background fill, and wide clips are cropped to follow faces. |
| 🎨 **Looks and effects** | It colour-grades each style, adds transitions, a title card, slow motion for action shots, film grain, vignette, cinematic letterbox bars and fades. It also stabilises shaky clips and fixes washed-out iPhone HDR colours. |
| 🔊 **Loudness** | It sets the volume to the level YouTube, Instagram and TikTok expect. |
| 📅 **Story order** | It puts everything in the order it was filmed, using the date inside each photo or video. |

## Offline and online

- **Offline:** after the first setup, everything works without internet: editing, effects, music and subtitles. The first setup needs internet once to download the parts it uses, including the speech model for subtitles.
- **Online:** every time you start AutoEdit, it checks for updates, installs new versions, styles, effects and music moods by itself, and then opens. If you're offline it skips the check and starts normally. Your own songs, fonts and finished videos are never changed.
- **New looks** come as style packs, which are small files in the `styles` folder. The first pack adds **Y2K**, **Moody** and **Dreamy**.

### Styles

- **Vlog**: bright and punchy with fast cuts, keeps you talking, upbeat music and captions.
- **Travel**: vibrant warm colours, energetic slide transitions and happy music.
- **Cinematic**: a movie look with 2.39:1 black bars, teal and orange colours, slow motion and an epic score.
- **Film**: nostalgic memories with warm vintage colours, grain and gentle piano.
- **Reels**: vertical 9:16 for TikTok, Reels and Shorts, with very fast beat cuts and big captions.
- **Chill**: a soft pastel look with lo-fi beats.
- **Y2K**: the digicam trend, with flashy colours, pixel and blur transitions, and grain.
- **Moody**: a dark aesthetic with muted colours and slow fades to black.
- **Dreamy**: a soft glow, pastel tones and gentle fades.

## Tips

- **Your own songs:** put them in `music/upbeat`, `music/chill`, `music/cinematic`, `music/emotional` or `music/happy`. You can also upload one song in the app under *More options*. Chart songs are copyrighted, so YouTube, TikTok or Instagram may mute or block your video. Free music you're allowed to use: YouTube Audio Library and Pixabay Music.
- **Different result:** press the button again. Every run picks slightly different transitions and a new piece of music.
- **Speed:** 720p is the fastest setting. A 1–2 minute video usually takes a few minutes. The first video with speech downloads the speech model once (about 0.5 GB).
- **Subtitles in Chinese, Japanese or Korean** use the fonts already on your computer. You can put your own `.ttf` font in the `fonts` folder.

## Moving to a new computer

Copy the whole AutoEdit folder to the new computer, for example with a USB drive or OneDrive. Then double-click START, and it sets itself up again for the new computer. Your songs, fonts and finished videos come along inside the folder. You can also download a fresh copy any time from github.com/michellewangty36/autoedit: click **Code**, then **Download ZIP**. If you do that, copy your `music` folder across from the old computer.

## For advanced users (command line)

```
python autoedit.py MyTripFolder --style cinematic --title "Bali 2026"
python autoedit.py clip1.mp4 photo.jpg --style reels --length 30 --lang zh
python autoedit.py --help
```

## Honest limits

AutoEdit is a clever automatic editor, not a human editor and not Premiere or CapCut. It judges shots by sharpness, faces, motion, brightness and speech, not by what is actually happening in them, so it can't tell a funny moment from a boring one. It has no manual timeline for fine-tuning individual cuts. If you want to polish the result by hand, open it in a free editor such as CapCut or DaVinci Resolve. The composed music is pleasant background music, not a hit song.

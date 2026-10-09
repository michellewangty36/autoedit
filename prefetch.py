"""Download the speech model once during setup, so subtitles also work offline later."""
try:
    from faster_whisper import WhisperModel
    print("Downloading the speech model for subtitles (about 0.5 GB, only once)...")
    WhisperModel("small", device="cpu", compute_type="int8")
    print("Speech model ready - subtitles will work offline.")
except Exception as e:
    print(f"Could not download the speech model now ({e}). It will try again when you make a video online.")

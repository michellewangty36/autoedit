"""Download the AI models once during setup, so everything also works offline later."""
try:
    from faster_whisper import WhisperModel
    print("Downloading the speech model for subtitles (about 0.5 GB, only once)...")
    WhisperModel("small", device="cpu", compute_type="int8")
    print("Speech model ready - subtitles will work offline.")
except Exception as e:
    print(f"Could not download the speech model now ({e}). It will try again when you make a video online.")
try:
    import ai_vision
    print("Downloading the smart AI that understands your shots (about 0.6 GB, only once)...")
    ai_vision.SmartEye()
    print("Smart AI ready - it works offline.")
except Exception as e:
    print(f"Could not download the smart AI now ({e}). It will try again when you make a video online.")

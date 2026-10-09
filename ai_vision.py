"""
Free offline "smart eye" for AutoEdit.

Uses CLIP (an open AI model that understands pictures and words) running on
your own computer through ONNX Runtime. After a one-time download (~0.6 GB,
stored in the models/ folder) it works completely offline and costs nothing.

What it does for the editor:
  * recognises what is in every shot (sunset, food, friends laughing, pets...)
  * scores shots by how "highlight-worthy" they look, and avoids accidental
    shots (floor, pocket, ceiling, screenshots)
  * follows your instruction, e.g. "focus on food and the beach"
  * spots near-identical shots so the video doesn't repeat itself
"""
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
IMAGE_MODEL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"

# what a good highlight looks like ...
POSITIVE = {
    "scenery": "a beautiful scenic landscape",
    "sunset": "a stunning sunset or sunrise sky",
    "beach": "a sunny beach with blue sea",
    "mountains": "majestic mountains and nature",
    "city": "a city skyline or a lively street",
    "night lights": "colorful city lights at night",
    "smiles": "people smiling and laughing together",
    "friends": "a group of friends having fun",
    "selfie": "a happy selfie of a person",
    "food": "delicious food on a table",
    "drinks": "a nicely served drink or coffee",
    "pets": "a cute dog or cat",
    "kids": "children playing happily",
    "celebration": "a birthday party or celebration with cake",
    "wedding": "a romantic wedding moment",
    "landmark": "a famous landmark or monument",
    "adventure": "an exciting outdoor adventure or sport",
    "performance": "a concert or live performance on stage",
    "flowers": "beautiful flowers in bloom",
    "aerial": "an amazing aerial view from above",
}
# ... and what a wasted shot looks like
NEGATIVE = [
    "a blurry out of focus photo",
    "a photo of the floor or the ground by accident",
    "the inside of a pocket or bag, very dark",
    "a plain white ceiling",
    "a screenshot of a phone screen",
    "a photo of a finger covering the camera lens",
    "an empty boring wall",
]


def _norm(x):
    x = np.asarray(x, dtype=np.float32)
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-9)


class SmartEye:
    def __init__(self, focus=None, log=print):
        from fastembed import ImageEmbedding, TextEmbedding
        MODELS.mkdir(exist_ok=True)
        self.log = log
        self.img = self._load(ImageEmbedding, IMAGE_MODEL)
        self.txt = self._load(TextEmbedding, TEXT_MODEL)
        names = list(POSITIVE)
        self.pos_names = names
        self.pos = _norm(list(self.txt.embed([POSITIVE[n] for n in names])))
        self.neg = _norm(list(self.txt.embed(NEGATIVE)))
        self.focus_terms = [t.strip() for t in (focus or "").replace(" and ", ",").split(",") if t.strip()]
        self.focus = _norm(list(self.txt.embed([f"a photo of {t}" for t in self.focus_terms]))) \
            if self.focus_terms else None

    @staticmethod
    def _load(cls, name):
        try:  # already downloaded -> works offline
            return cls(name, cache_dir=str(MODELS), local_files_only=True)
        except Exception:
            return cls(name, cache_dir=str(MODELS))  # first time: download once

    def embed_images(self, images, batch_size=16):
        """images: list of PIL images or numpy RGB arrays."""
        from PIL import Image
        ims = [Image.fromarray(i) if isinstance(i, np.ndarray) else i for i in images]
        if not ims:
            return np.zeros((0, self.pos.shape[1]), np.float32)
        return _norm(list(self.img.embed(ims, batch_size=batch_size)))

    def score(self, emb):
        """Return (highlight score per image, top label per image).

        Scores are roughly in -1..+1: >0 looks like a highlight, <0 like a wasted shot.
        """
        if len(emb) == 0:
            return np.zeros(0), []
        sp = emb @ self.pos.T            # cosine similarities, typically 0.15-0.35 for CLIP
        sn = emb @ self.neg.T
        best_pos = sp.max(axis=1)
        best_neg = sn.max(axis=1)
        s = (best_pos - best_neg) * 25    # CLIP similarity gaps are small; scale to ~ -1..1
        if self.focus is not None:
            sf = (emb @ self.focus.T).max(axis=1)
            s = s + (sf - np.median(sf)) * 60  # strong boost for what you asked for
        labels = [self.pos_names[i] for i in sp.argmax(axis=1)]
        return np.clip(s, -1.5, 2.5), labels


def load(focus=None, log=print):
    """Return a SmartEye, or None if the AI is not installed / not downloaded and offline."""
    try:
        eye = SmartEye(focus, log)
        return eye
    except ImportError:
        log("  (smart AI not installed - run START again to install it)")
    except Exception as e:
        log(f"  (smart AI not available: {e}. Connect to the internet once to download it.)")
    return None

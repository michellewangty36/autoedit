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

# "what are we doing now" captions: what CLIP should look for -> (English, Chinese, Chinese-English mix)
ACTIVITIES = {
    "coconut boat": ("people riding a round bamboo basket boat on a river", "Trying the coconut boat today!", "今天来坐椰子船~", "今天来尝试 coconut boat la~"),
    "boat trip": ("a boat ride on a river or the sea", "Boat ride time!", "坐船去咯~", "坐 boat 游河 la~"),
    "fishing": ("a fisherman throwing a fishing net from a boat", "Watching the fishermen at work", "看渔夫撒网~", "看人家撒网捉鱼 wow~"),
    "cable car": ("a cable car gondola high above mountains", "Up we go on the cable car!", "坐缆车上山咯~", "坐 cable car 上山 la~"),
    "theme park": ("an amusement park with rides and castles", "Theme park day!", "今天去游乐园~", "今天去 theme park 玩 la~"),
    "show": ("a stage show with dancers in costumes", "Catching a show", "来看表演~", "来看 show 咯~"),
    "lanterns": ("colorful lanterns glowing at night in an old town", "Lantern night", "看灯笼夜景~", "晚上看 lantern 好美~"),
    "old town": ("a historic old town street with yellow buildings", "Exploring the old town", "逛古镇咯~", "来逛 old town la~"),
    "night view": ("city lights reflecting on a river at night", "Night views", "看夜景~", "晚上的 view 太美了~"),
    "bridge": ("a big bridge lit up at night", "That bridge though!", "来看大桥~", "来看 bridge 咯~"),
    "temple": ("a buddhist temple or pagoda", "Visiting the temple", "参观寺庙~", "来参观 temple~"),
    "church": ("a church or cathedral building", "Visiting the church", "来看教堂~", "来看 church 咯~"),
    "beach": ("people at a sunny beach with blue sea", "Beach time!", "去海边咯~", "Beach time la~"),
    "pool": ("a hotel swimming pool", "Pool time", "泡泳池~", "去 pool 泡一下~"),
    "sunset": ("a sunset sky over the water", "Sunset vibes", "看日落~", "看 sunset 咯~"),
    "mountains": ("hiking on a green mountain trail", "Into the mountains", "去爬山~", "去爬山 hiking la~"),
    "waterfall": ("a waterfall in the forest", "Waterfall!", "看瀑布~", "来看 waterfall~"),
    "garden": ("a beautiful flower garden", "Flower garden", "逛花园~", "来逛 garden 拍照~"),
    "seafood": ("a seafood dinner with prawns lobster and crab", "Seafood feast!", "吃海鲜大餐~", "今天来吃 seafood la~"),
    "dinner": ("a restaurant table full of dishes", "Dinner time", "开饭咯~", "Dinner time 开吃 la~"),
    "street food": ("street food from a roadside stall", "Street food hunting", "吃街边小吃~", "来吃 street food 咯~"),
    "noodles": ("a bowl of noodle soup like pho", "Noodle time", "来一碗粉~", "来一碗 pho la~"),
    "coffee": ("a cup of coffee in a cafe", "Coffee break", "喝咖啡~", "Coffee break 一下~"),
    "drinks": ("holding a cold drink or bubble tea in hand", "Something to drink", "喝点东西~", "来一杯 drink 先~"),
    "dessert": ("desserts cakes and pastries", "Something sweet", "吃甜点~", "吃 dessert 咯~"),
    "bakery": ("a bakery shop display with breads and pastries", "Bakery stop", "去买面包~", "去 bakery 买东西吃~"),
    "market": ("a busy local market with stalls", "Market walk", "逛市场~", "来逛 market la~"),
    "shopping": ("shopping in a mall or shop", "Shopping time", "去逛街~", "Shopping time 买买买~"),
    "souvenirs": ("a pile of snacks and souvenirs bought on a trip", "Souvenir haul", "买手信咯~", "买了好多手信 la~"),
    "hotel": ("a hotel room with a bed and suitcase", "Hotel check-in", "Check-in 酒店~", "Check-in hotel 咯~"),
    "airport": ("an airport terminal or an airplane on the tarmac", "Off we go!", "出发咯~", "出发去 airport la~"),
    "plane view": ("the view from an airplane window", "Flying high", "在飞机上~", "Flying 咯~"),
    "road trip": ("a view of the road from inside a car or bus", "On the road", "坐车出发~", "坐车出发 la~"),
    "motorbike": ("riding a motorbike on a busy street", "Motorbike ride", "坐摩托~", "坐 motorbike 兜风~"),
    "city walk": ("walking on a city street with buildings", "City walk", "逛逛市区~", "City walk 一下~"),
    "group photo": ("a group of friends posing together for a photo", "Squad photo!", "合照时间~", "合照 time~"),
}


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
        self.act_names = list(ACTIVITIES)
        self.acts = _norm(list(self.txt.embed([ACTIVITIES[n][0] for n in self.act_names])))

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

    def activity(self, emb):
        """Best guess of what is happening in one shot: (name, confidence 0..1) or (None, 0)."""
        if emb is None:
            return None, 0.0
        sims = self.acts @ np.asarray(emb, dtype=np.float32)
        p = np.exp((sims - sims.max()) * 100)
        p /= p.sum()
        k = int(np.argmax(p))
        return self.act_names[k], float(p[k])


def caption_for(activity, lang="mix", place=None):
    """Turn an activity (and maybe a place) into a short on-screen caption."""
    if lang == "off":
        return ""
    idx = {"en": 1, "zh": 2}.get(lang, 3)
    if activity in ACTIVITIES:
        return ACTIVITIES[activity][idx]
    if place:
        return {"en": f"Hello {place}!", "zh": f"来到{place}啦~"}.get(lang, f"来到 {place} 啦~")
    return ""


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

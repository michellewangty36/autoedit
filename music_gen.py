"""
Royalty-free background music generator.

Every track is synthesised from scratch with numpy, so there is no copyright
on it at all: you can post the result on YouTube, TikTok, Instagram, etc.
A new track is composed for every video, exactly as long as the video,
with an intro, a main part and a proper ending.

Moods: upbeat, chill, cinematic, emotional, happy
"""
import numpy as np

SR = 44100

NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
        "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}

MOODS = {
    #            bpm  key   scale    progression (scale degrees, 0-based)  chord type
    "upbeat":    dict(bpm=118, key="G", minor=False, prog=[0, 4, 5, 3], sevenths=False, drums="four"),
    "happy":     dict(bpm=126, key="C", minor=False, prog=[0, 3, 4, 3], sevenths=False, drums="four"),
    "chill":     dict(bpm=84, key="F", minor=False, prog=[3, 2, 5, 0], sevenths=True, drums="lofi"),
    "cinematic": dict(bpm=72, key="D", minor=True, prog=[0, 5, 2, 6], sevenths=False, drums="epic"),
    "emotional": dict(bpm=70, key="Eb", minor=False, prog=[0, 4, 5, 3], sevenths=False, drums="soft"),
}

MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]


def midi_to_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def _lowpass(x, cutoff):
    try:
        from scipy.signal import butter, lfilter
        b, a = butter(2, min(cutoff / (SR / 2), 0.99))
        return lfilter(b, a, x, axis=0)
    except ImportError:  # crude fallback: moving average
        n = max(1, int(SR / cutoff / 2))
        k = np.ones(n) / n
        return np.convolve(x, k, mode="same")


def _env(n, a=0.01, d=0.1, s=0.7, r=0.2):
    """ADSR envelope of n samples (times in seconds)."""
    a_n, d_n, r_n = int(a * SR), int(d * SR), int(r * SR)
    s_n = max(0, n - a_n - d_n - r_n)
    e = np.concatenate([
        np.linspace(0, 1, max(a_n, 1)),
        np.linspace(1, s, max(d_n, 1)),
        np.full(s_n, s),
        np.linspace(s, 0, max(r_n, 1)),
    ])
    return e[:n] if len(e) >= n else np.pad(e, (0, n - len(e)))


# ---------------------------------------------------------------- instruments
def inst_pad(freq, dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.zeros(n)
    for det in (-0.12, 0.0, 0.12):  # detuned saws
        f = freq * 2 ** (det / 12)
        ph = rng.random()
        sig += 2 * ((t * f + ph) % 1.0) - 1
    sig = _lowpass(sig / 3, 1400)
    return sig * _env(n, a=min(0.6, dur / 3), d=0.3, s=0.8, r=min(0.8, dur / 3))


def inst_pluck(freq, dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = (np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(4 * np.pi * freq * t)
           + 0.25 * np.sin(6 * np.pi * freq * t))
    return sig * np.exp(-t * 7) * _env(n, a=0.003, d=0.05, s=1, r=0.05)


def inst_piano(freq, dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.zeros(n)
    for h, amp in enumerate([1, 0.45, 0.25, 0.12, 0.06], start=1):
        sig += amp * np.sin(2 * np.pi * freq * h * t * (1 + 0.0004 * h)) * np.exp(-t * (1.6 + h * 0.9))
    return sig * _env(n, a=0.004, d=0.05, s=1, r=0.12)


def inst_epiano(freq, dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t + 1.2 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 4))
    trem = 1 + 0.15 * np.sin(2 * np.pi * 4.5 * t)
    return sig * trem * np.exp(-t * 1.5) * _env(n, a=0.005, d=0.1, s=1, r=0.15)


def inst_bass(freq, dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sign(np.sin(2 * np.pi * freq * t))
    return _lowpass(sig, 600) * _env(n, a=0.005, d=0.1, s=0.6, r=0.05)


def drum_kick(rng, big=False):
    n = int((0.6 if big else 0.35) * SR)
    t = np.arange(n) / SR
    f = (45 if big else 50) + 110 * np.exp(-t * 30)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * (5 if big else 9))


def drum_snare(rng, soft=False):
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    noise = _lowpass(rng.standard_normal(n), 6000) * np.exp(-t * (22 if soft else 16))
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)
    return (noise * (0.5 if soft else 0.8) + tone * 0.4)


def drum_hat(rng):
    n = int(0.06 * SR)
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    x = x - _lowpass(x, 7000)  # high-pass
    return x * np.exp(-t * 60) * 0.5


def drum_tom(rng):
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    f = 70 + 60 * np.exp(-t * 12)
    ph = 2 * np.pi * np.cumsum(f) / SR
    noise = _lowpass(rng.standard_normal(n), 900) * np.exp(-t * 14) * 0.4
    return (np.sin(ph) * np.exp(-t * 4) + noise)


# ---------------------------------------------------------------- helpers
def _add(buf, sig, start, gain=1.0, pan=0.0):
    i = int(start * SR)
    if i >= len(buf):
        return
    sig = sig[: len(buf) - i]
    l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    buf[i:i + len(sig), 0] += sig * gain * l
    buf[i:i + len(sig), 1] += sig * gain * r


def _reverb(x, seconds=2.0, mix=0.25, rng=None):
    rng = rng or np.random.default_rng(1)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.empty_like(x)
    for ch in range(2):
        ir = rng.standard_normal(n) * np.exp(-t * 3.0 / seconds)
        ir[0] = 0
        ir /= np.sqrt(np.sum(ir ** 2))
        size = 1 << int(np.ceil(np.log2(len(x) + n)))
        wet = np.fft.irfft(np.fft.rfft(x[:, ch], size) * np.fft.rfft(ir, size), size)[: len(x)]
        out[:, ch] = x[:, ch] * (1 - mix) + wet * mix
    return out


def chord_notes(root_midi, degree, minor, seventh):
    scale = MINOR if minor else MAJOR
    idx = [degree, degree + 2, degree + 4] + ([degree + 6] if seventh else [])
    notes = []
    for i in idx:
        octave, step = divmod(i, 7)
        notes.append(root_midi + scale[step] + 12 * octave)
    return notes


# ---------------------------------------------------------------- composer
def generate(duration, mood="upbeat", seed=0, bpm=None):
    """Return (stereo float32 array, sample_rate, beat_seconds, first_beat_offset)."""
    cfg = dict(MOODS.get(mood, MOODS["upbeat"]))
    mood = cfg.get("sound", mood if mood in ("upbeat", "happy", "chill", "cinematic", "emotional") else "upbeat")
    rng = np.random.default_rng(seed)
    if bpm:
        cfg["bpm"] = bpm
    beat = 60.0 / cfg["bpm"]
    bar = beat * 4
    total = duration
    buf = np.zeros((int((total + 4) * SR), 2))
    root = 48 + NOTE[cfg["key"]]  # C3-ish
    n_bars = int(np.ceil(total / bar))
    prog = cfg["prog"]
    drums = cfg["drums"]
    # sections: intro (first 2 bars), main, outro (last bar = final chord)
    intro_bars = 2 if n_bars > 6 else 1
    melody_scale = MINOR if cfg["minor"] else MAJOR
    motif = rng.choice([0, 2, 4, 4, 7, 5, 2, 0], size=8)  # small repeating melody motif

    for b in range(n_bars):
        t0 = b * bar
        deg = prog[b % len(prog)]
        notes = chord_notes(root, deg, cfg["minor"], cfg["sevenths"])
        last = b == n_bars - 1
        main = b >= intro_bars and not last
        progress = b / max(1, n_bars - 1)

        # pad / chords on every bar
        dur = bar + (2.5 if last else 0.15)
        for m in notes:
            if mood in ("emotional",):
                continue
            _add(buf, inst_pad(midi_to_hz(m + 12), dur, rng), t0, 0.10 if mood != "cinematic" else 0.16,
                 pan=rng.uniform(-0.4, 0.4))

        if last:
            # final chord ring-out
            for m in notes:
                f = inst_piano if mood in ("emotional", "cinematic") else inst_pluck
                _add(buf, f(midi_to_hz(m + 12), 3.0, rng), t0, 0.22)
            _add(buf, inst_bass(midi_to_hz(notes[0] - 12), 2.5, rng), t0, 0.35)
            if drums in ("four", "epic"):
                _add(buf, drum_kick(rng, big=True), t0, 0.8)
            continue

        # bass
        if main or mood == "cinematic":
            step = beat / 2 if drums == "four" else beat
            k = 0
            while k * step < bar - 1e-6:
                note = notes[0] - 12 if k % 4 != 3 else notes[2] - 12
                _add(buf, inst_bass(midi_to_hz(note), step * 0.9, rng), t0 + k * step, 0.30)
                k += 1

        # arpeggio / piano
        if mood in ("upbeat", "happy"):
            for k in range(8):
                m = notes[k % len(notes)] + 24
                _add(buf, inst_pluck(midi_to_hz(m), beat / 2, rng), t0 + k * beat / 2, 0.12,
                     pan=0.3 if k % 2 else -0.3)
        elif mood == "chill":
            for k, off in enumerate([0, 1.5, 2.5]):
                for m in notes:
                    _add(buf, inst_epiano(midi_to_hz(m + 12), beat * 1.5, rng), t0 + off * beat, 0.07)
        elif mood == "emotional":
            for k in range(8):
                m = notes[[0, 1, 2, 1, 3 % len(notes), 2, 1, 2][k] % len(notes)] + 12
                _add(buf, inst_piano(midi_to_hz(m), beat * 2, rng), t0 + k * beat / 2, 0.16)
            _add(buf, inst_piano(midi_to_hz(notes[0] - 12), bar, rng), t0, 0.18)
        elif mood == "cinematic" and progress > 0.3:
            for k in range(8):  # string-like ostinato
                m = notes[k % 2 * 2] + 12
                _add(buf, inst_pluck(midi_to_hz(m), beat / 2, rng), t0 + k * beat / 2, 0.08 + 0.08 * progress)

        # melody on main sections (every other bar, simple motif)
        if main and b % 2 == 1 and mood != "cinematic":
            for k in range(4):
                deg_m = (deg + motif[(b + k) % 8]) % 7
                m = root + 24 + melody_scale[deg_m]
                f = inst_piano if mood == "emotional" else inst_pluck
                _add(buf, f(midi_to_hz(m), beat * 1.2, rng), t0 + k * beat, 0.11)

        # drums
        if not main and drums != "epic":
            continue
        for k in range(4):
            tb = t0 + k * beat
            if drums == "four":
                _add(buf, drum_kick(rng), tb, 0.75)
                if k in (1, 3):
                    _add(buf, drum_snare(rng), tb, 0.35)
                _add(buf, drum_hat(rng), tb + beat / 2, 0.14)
                _add(buf, drum_hat(rng), tb, 0.06)
            elif drums == "lofi":
                swing = beat * 0.08
                if k in (0, 2):
                    _add(buf, drum_kick(rng), tb, 0.55)
                if k == 2:
                    _add(buf, drum_kick(rng), tb + beat / 2 + swing, 0.35)
                if k in (1, 3):
                    _add(buf, drum_snare(rng, soft=True), tb, 0.30)
                _add(buf, drum_hat(rng), tb, 0.10)
                _add(buf, drum_hat(rng), tb + beat / 2 + swing, 0.08)
            elif drums == "soft" and progress > 0.5:
                if k == 0:
                    _add(buf, drum_kick(rng), tb, 0.35)
                if k == 2:
                    _add(buf, drum_snare(rng, soft=True), tb, 0.12)
            elif drums == "epic":
                if progress > 0.45 and k in (0, 2):
                    _add(buf, drum_tom(rng), tb, 0.45 + 0.3 * progress)
                if progress > 0.7 and k in (1, 3):
                    _add(buf, drum_tom(rng), tb + beat / 2, 0.25)
                if k == 0 and b % 4 == 0 and b >= intro_bars:
                    _add(buf, drum_kick(rng, big=True), tb, 0.6)

    if mood == "chill":  # vinyl crackle
        crackle = (rng.random(len(buf)) > 0.9993) * rng.standard_normal(len(buf)) * 0.15
        buf += _lowpass(crackle, 4000)[:, None] * np.array([1, 1])
    buf = _reverb(buf, seconds=3.0 if mood in ("cinematic", "emotional") else 1.6,
                  mix=0.32 if mood in ("cinematic", "emotional") else 0.18, rng=rng)
    buf = buf[: int(total * SR)]
    # fade in / fade out
    fi, fo = int(0.5 * SR), int(min(3.0, total / 4) * SR)
    buf[:fi] *= np.linspace(0, 1, fi)[:, None]
    buf[-fo:] *= np.linspace(1, 0, fo)[:, None]
    peak = np.max(np.abs(buf)) or 1.0
    buf = np.tanh(buf / peak * 1.3) * 0.85  # gentle soft-clip "mastering"
    return buf.astype(np.float32), SR, beat, 0.0


def write_wav(path, data, sr=SR):
    import wave
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    import sys
    mood = sys.argv[1] if len(sys.argv) > 1 else "upbeat"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    data, sr, beat, _ = generate(secs, mood)
    write_wav(f"demo_{mood}.wav", data, sr)
    print(f"wrote demo_{mood}.wav ({secs}s, beat {beat:.3f}s)")

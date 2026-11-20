"""
Drag-and-drop timeline for the Edit tab (like a simple CapCut).

  * drag a shot to move it
  * drag the right edge of a shot to make it longer or shorter
  * ✕ removes a shot (it goes to "Removed", press ↩ to put it back)
  * tap a shot to preview it and change its length, start, speed, effect and text

The page script below writes every change into a hidden text box; app.py turns that into the shot table
that "Re-make video" uses.
"""
import html
import json
from pathlib import Path
from urllib.parse import quote

import gradio as gr

try:
    _GR_MAJOR = int(gr.__version__.split(".")[0])
except Exception:
    _GR_MAJOR = 5
FILE_ROUTE = "/gradio_api/file=" if _GR_MAJOR >= 5 else "/file="


def file_url(path):
    return FILE_ROUTE + quote(str(Path(path).resolve()).replace("\\", "/"), safe="/:")


def render(plan, thumbs, effects, transitions, looks=(), positions=(), sizes=(), preview=None):
    """preview: dict(w, h, music, music_volume, mute, text_look, text_color, text_pos, text_size) for the ▶ player."""
    """HTML for the timeline. thumbs: {shot index: thumbnail path}."""
    if not plan or not plan.get("shots"):
        return "<div id='ae-tl' class='ae-empty'>Your timeline appears here after you make a video.</div>"
    shots = plan["shots"]
    order = sorted(range(len(shots)), key=lambda i: float(shots[i].get("order", i + 1)))

    def card(i):
        sh = shots[i]
        dur = float(sh.get("dur", 3))
        mx = ""
        if sh["kind"] == "video":
            full = float((sh.get("info") or {}).get("duration") or 0)
            if full:
                mx = f"{max(0.8, (full - float(sh.get('start', 0))) / max(float(sh.get('speed', 1)), 0.1)):.2f}"
        th = thumbs.get(i)
        img = f"<img src='{html.escape(file_url(th))}' draggable='false' alt=''>" if th else "<div class='ae-noimg'>🎞️</div>"
        text = str(sh.get("text") or "")
        keep = sh.get("keep", True)
        attrs = dict(id=i, dur=f"{dur:.2f}", start=f"{float(sh.get('start', 0)):.2f}", speed=sh.get("speed", 1.0),
                     effect=sh.get("effect", "none"), transition=sh.get("transition", "auto"), text=text,
                     kind=sh["kind"], src=file_url(sh["path"]), name=Path(sh["path"]).name, max=mx,
                     mute="off" if sh.get("mute") else "on", tlook=sh.get("text_look") or "default",
                     tcolor=sh.get("text_color") or "", tpos=sh.get("text_pos") or "default",
                     tsize=sh.get("text_size") or "default")
        data = " ".join(f"data-{k}='{html.escape(str(v), quote=True)}'" for k, v in attrs.items())
        return (f"<div class='ae-card' {data} style='width:{max(56, dur * 40):.0f}px'>{img}"
                f"<span class='ae-dur'>{dur:.1f}s</span>"
                f"<span class='ae-txt'{'' if text else ' hidden'}>💬</span>"
                f"<span class='ae-kind'>{'🎬' if sh['kind'] == 'video' else '📷'}</span>"
                f"<button class='ae-x' title='{'Remove' if keep else 'Put back'}'>{'✕' if keep else '↩'}</button>"
                f"<div class='ae-handle' title='Drag to make longer or shorter'></div></div>")

    kept = "".join(card(i) for i in order if shots[i].get("keep", True))
    removed = "".join(card(i) for i in order if not shots[i].get("keep", True))
    total = sum(float(shots[i].get("dur", 3)) for i in order if shots[i].get("keep", True))
    n = sum(1 for sh in shots if sh.get("keep", True))
    opts = html.escape(json.dumps(dict(effects=list(effects), transitions=["auto"] + list(transitions),
                                       looks=["default"] + list(looks), positions=["default"] + list(positions),
                                       sizes=["default"] + list(sizes))), quote=True)
    pv = dict(preview or {})
    if pv.get("music"):
        pv["music"] = file_url(pv["music"])
    pv_attr = html.escape(json.dumps(pv), quote=True)
    return (f"<div id='ae-tl' data-opts='{opts}' data-preview='{pv_attr}'>"
            f"<div class='ae-player'><div class='ae-stage'><div class='ae-layer'></div><div class='ae-layer'></div>"
            f"<div class='ae-ptext'></div><div class='ae-pbig'>▶</div></div>"
            f"<div class='ae-pbar'><button class='ae-play'>▶ Play</button><span class='ae-ptime'>0:00</span>"
            f"<span class='ae-pnote'>Quick preview. Press Re-make for the final video.</span></div></div>"
            f"<div class='ae-tl-help'>Drag a shot to move it · drag its right edge to change the length · "
            f"✕ removes it · tap it to preview and edit</div>"
            f"<div class='ae-total'>Total about {round(total)}s · {n} shots</div>"
            f"<div class='ae-track'>{kept}</div>"
            f"<div class='ae-panel'><div class='ae-panel-empty'>Tap a shot to preview and edit it.</div></div>"
            f"<div class='ae-removed'><b>Removed</b> (↩ puts it back)<div class='ae-removed-list'>{removed}</div></div>"
            f"</div>")


def apply(plan, data):
    """Apply the timeline's changes (JSON from the page) to the plan."""
    try:
        d = json.loads(data or "{}")
    except ValueError:
        return plan
    shots = plan.get("shots") or []
    for r in d.get("shots", []):
        try:
            i = int(r["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= i < len(shots):
            continue
        sh = shots[i]
        sh["keep"] = bool(r.get("keep", True))
        if sh["keep"]:
            sh["order"] = float(r.get("order", sh.get("order", i + 1)))
        else:
            sh["order"] = 10000 + i
        try:
            sh["dur"] = max(0.8, round(float(r.get("dur", sh.get("dur", 3))), 2))
            if sh["kind"] == "video":
                sh["start"] = max(0.0, round(float(r.get("start", sh.get("start", 0))), 2))
                sh["speed"] = min(4.0, max(0.25, float(r.get("speed", sh.get("speed", 1)))))
        except (TypeError, ValueError):
            pass
        if r.get("effect"):
            sh["effect"] = str(r["effect"])
        if r.get("transition"):
            sh["transition"] = str(r["transition"])
        sh["text"] = str(r.get("text") or "")
        for k, key in (("tlook", "text_look"), ("tpos", "text_pos"), ("tsize", "text_size"), ("tcolor", "text_color")):
            v = str(r.get(k) or "")
            if v and v != "default":
                sh[key] = v
            else:
                sh.pop(key, None)
        if sh["kind"] == "video" and r.get("mute") in ("on", "off"):
            sh["mute"] = r["mute"] == "off"
    return plan


CSS = """
#ae-tl .ae-player { margin-bottom: 12px; }
#ae-tl .ae-stage { position: relative; background: #000; border-radius: 12px; overflow: hidden; margin: 0 auto;
  max-width: 100%; cursor: pointer; }
#ae-tl .ae-layer { position: absolute; inset: 0; opacity: 0; transition: opacity .35s; }
#ae-tl .ae-layer.on { opacity: 1; }
#ae-tl .ae-layer video, #ae-tl .ae-layer img { width: 100%; height: 100%; object-fit: cover; }
#ae-tl .ae-layer.fit video, #ae-tl .ae-layer.fit img { object-fit: contain; }
#ae-tl .ae-ptext { position: absolute; left: 6%; right: 6%; text-align: center; pointer-events: none;
  font-weight: 700; line-height: 1.35; }
#ae-tl .ae-ptext span { display: inline-block; padding: .15em .5em; border-radius: .35em; }
#ae-tl .ae-pbig { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-size: 54px; color: rgba(255,255,255,.9); text-shadow: 0 2px 10px #000; }
#ae-tl .ae-pbar { display: flex; gap: 10px; align-items: center; justify-content: center; margin-top: 6px;
  flex-wrap: wrap; }
#ae-tl .ae-play { padding: 6px 18px; border-radius: 999px; border: none; background: #d946ef; color: #fff;
  font-weight: 700; cursor: pointer; font-size: 15px; }
#ae-tl .ae-pnote { font-size: 12px; opacity: .65; }
#ae-tl .ae-card.ae-now { border-color: #22d3ee; }
@keyframes ae-zin { from { transform: scale(1); } to { transform: scale(1.14); } }
@keyframes ae-zout { from { transform: scale(1.14); } to { transform: scale(1); } }
@keyframes ae-punch { from { transform: scale(1.18); } to { transform: scale(1); } }
@keyframes ae-shake { 0%,100% { transform: translate(0,0) scale(1.05); } 25% { transform: translate(-1.5%,1%) scale(1.05); }
  50% { transform: translate(1.5%,-1%) scale(1.05); } 75% { transform: translate(-1%,-1%) scale(1.05); } }
@keyframes ae-flash { from { filter: brightness(3); } to { filter: brightness(1); } }
@keyframes ae-blurin { from { filter: blur(14px); } to { filter: blur(0); } }
@keyframes ae-glitch { 0%,100% { filter: hue-rotate(0); transform: translate(0); } 20% { filter: hue-rotate(40deg) saturate(1.6); transform: translate(-2px,1px); }
  40% { filter: hue-rotate(-40deg); transform: translate(2px,-1px); } 60% { filter: saturate(1.3); transform: translate(0); } }
#ae-tl-data { display: none !important; }
#ae-tl { user-select: none; -webkit-user-select: none; }
#ae-tl.ae-empty { padding: 18px; opacity: .7; text-align: center; }
#ae-tl .ae-tl-help { font-size: 13px; opacity: .75; margin-bottom: 4px; }
#ae-tl .ae-total { font-weight: 600; margin-bottom: 8px; }
#ae-tl .ae-track, #ae-tl .ae-removed-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: stretch;
  padding: 8px; border-radius: 12px; background: rgba(127,127,127,.12); min-height: 84px; }
#ae-tl .ae-removed { margin-top: 12px; font-size: 13px; }
#ae-tl .ae-removed-list { min-height: 40px; opacity: .65; }
#ae-tl .ae-card { position: relative; height: 72px; border-radius: 10px; overflow: hidden; cursor: grab;
  background: #333; flex: 0 0 auto; touch-action: none; box-shadow: 0 1px 3px rgba(0,0,0,.35);
  border: 2px solid transparent; }
#ae-tl .ae-card.ae-sel { border-color: #d946ef; }
#ae-tl .ae-card img, #ae-tl .ae-noimg { width: 100%; height: 100%; object-fit: cover; pointer-events: none;
  display: flex; align-items: center; justify-content: center; font-size: 24px; }
#ae-tl .ae-dur { position: absolute; left: 4px; bottom: 3px; font-size: 11px; color: #fff;
  background: rgba(0,0,0,.6); padding: 0 4px; border-radius: 4px; pointer-events: none; }
#ae-tl .ae-kind { position: absolute; left: 3px; top: 2px; font-size: 11px; pointer-events: none; }
#ae-tl .ae-txt { position: absolute; right: 14px; bottom: 3px; font-size: 12px; pointer-events: none; }
#ae-tl .ae-x { position: absolute; right: 2px; top: 2px; width: 22px; height: 22px; border-radius: 50%;
  border: none; background: rgba(0,0,0,.65); color: #fff; font-size: 12px; line-height: 22px; padding: 0;
  cursor: pointer; }
#ae-tl .ae-handle { position: absolute; right: 0; top: 26px; bottom: 0; width: 12px; cursor: ew-resize;
  background: linear-gradient(90deg, transparent, rgba(217,70,239,.85)); }
#ae-tl .ae-ghost { position: fixed; z-index: 9999; opacity: .85; pointer-events: none; cursor: grabbing;
  box-shadow: 0 8px 20px rgba(0,0,0,.45); }
#ae-tl .ae-place { border: 2px dashed #d946ef; background: rgba(217,70,239,.15); border-radius: 10px;
  height: 72px; flex: 0 0 auto; }
#ae-tl .ae-panel { margin-top: 10px; display: flex; gap: 14px; flex-wrap: wrap; align-items: flex-start; }
#ae-tl .ae-panel-empty { opacity: .65; font-size: 13px; }
#ae-tl .ae-prev video, #ae-tl .ae-prev img { max-width: min(420px, 100%); max-height: 260px; border-radius: 10px;
  background: #000; }
#ae-tl .ae-prev { flex: 1 1 260px; }
#ae-tl .ae-fields { flex: 1 1 220px; display: grid; gap: 8px; user-select: text; -webkit-user-select: text; }
#ae-tl .ae-fields label { display: grid; gap: 2px; font-size: 13px; }
#ae-tl .ae-fields input, #ae-tl .ae-fields select { padding: 6px 8px; border-radius: 8px;
  border: 1px solid rgba(127,127,127,.5); background: transparent; color: inherit; font-size: 15px; }
#ae-tl .ae-fields select option { color: #000; }
#ae-tl .ae-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
#ae-tl .ae-fields input[type=color] { height: 38px; padding: 2px; }
#ae-tl .ae-fields .ae-name { font-weight: 600; word-break: break-all; }
"""

JS = r"""
<script>
(function () {
  if (window.__aeTimeline) return;
  window.__aeTimeline = true;
  const PPS = 40, MIN = 0.8;
  const root = () => document.getElementById('ae-tl');
  const track = () => root() && root().querySelector('.ae-track');
  const cards = () => [...track().querySelectorAll('.ae-card')];
  const fmt = d => (Math.round(d * 10) / 10).toFixed(1) + 's';
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  function setW(c) {
    const d = parseFloat(c.dataset.dur);
    c.style.width = Math.max(56, d * PPS) + 'px';
    c.querySelector('.ae-dur').textContent = fmt(d);
  }
  function total() {
    const t = cards().reduce((a, c) => a + parseFloat(c.dataset.dur), 0);
    const e = root().querySelector('.ae-total');
    if (e) e.textContent = 'Total about ' + Math.round(t) + 's · ' + cards().length + ' shots';
  }
  function commit() {
    const out = [];
    let o = 1;
    root().querySelectorAll('.ae-card').forEach(c => {
      const keep = !!c.closest('.ae-track');
      out.push({id: +c.dataset.id, keep, order: keep ? o++ : 9999, dur: +c.dataset.dur, start: +c.dataset.start,
                speed: +c.dataset.speed, effect: c.dataset.effect, transition: c.dataset.transition,
                text: c.dataset.text || '', mute: c.dataset.mute || 'on', tlook: c.dataset.tlook,
                tcolor: c.dataset.tcolor, tpos: c.dataset.tpos, tsize: c.dataset.tsize});
    });
    total();
    const ta = document.querySelector('#ae-tl-data textarea');
    if (!ta) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, JSON.stringify({t: Date.now(), shots: out}));
    ta.dispatchEvent(new Event('input', {bubbles: true}));
  }
  function select(c) {
    window.__aeSel = c.dataset.id;
    root().querySelectorAll('.ae-card').forEach(k => k.classList.toggle('ae-sel', k === c));
    const opts = JSON.parse(root().dataset.opts || '{}');
    const v = c.dataset.kind === 'video';
    const s = parseFloat(c.dataset.start), d = parseFloat(c.dataset.dur), sp = parseFloat(c.dataset.speed) || 1;
    const sel = (k, list, cur) => `<select data-k="${k}">` +
      list.map(x => `<option value="${esc(x)}"${String(x) === String(cur) ? ' selected' : ''}>${esc(x)}</option>`).join('') + '</select>';
    root().querySelector('.ae-panel').innerHTML =
      `<div class="ae-prev">` + (v ? `<video src="${esc(c.dataset.src)}#t=${s.toFixed(2)},${(s + d * sp).toFixed(2)}" controls playsinline preload="metadata"></video>`
                                    : `<img src="${esc(c.dataset.src)}" alt="">`) + `</div>` +
      `<div class="ae-fields"><div class="ae-name">${esc(c.dataset.name)}</div>` +
      `<label>Length (seconds)<input type="number" step="0.1" min="0.8" data-k="dur" value="${d.toFixed(1)}"></label>` +
      (v ? `<label>Start at (seconds into the clip)<input type="number" step="0.1" min="0" data-k="start" value="${s.toFixed(1)}"></label>` +
           `<label>Speed (0.5 = slow motion)${sel('speed', ['0.5', '0.75', '1', '1.5', '2'], String(sp))}</label>` +
           `<label>Sound of this clip${sel('mute', ['on', 'off'], c.dataset.mute || 'on')}</label>` : '') +
      `<label>Text on screen<input type="text" data-k="text" value="${esc(c.dataset.text || '')}" placeholder="e.g. 今天来尝试 coconut boat la~"></label>` +
      `<div class="ae-row"><label>Text look${sel('tlook', opts.looks || ['default'], c.dataset.tlook)}</label>` +
      `<label>Colour<input type="color" data-k="tcolor" value="${esc(c.dataset.tcolor || '#ffffff')}"></label></div>` +
      `<div class="ae-row"><label>Where${sel('tpos', opts.positions || ['default'], c.dataset.tpos)}</label>` +
      `<label>Size${sel('tsize', opts.sizes || ['default'], c.dataset.tsize)}</label></div>` +
      `<label>Effect${sel('effect', opts.effects || ['none'], c.dataset.effect)}</label>` +
      `<label>Transition in${sel('transition', opts.transitions || ['auto'], c.dataset.transition)}</label></div>`;
  }
  // remove / put back
  document.addEventListener('click', e => {
    const x = e.target.closest('#ae-tl .ae-x');
    if (!x) return;
    e.preventDefault();
    const c = x.closest('.ae-card');
    if (c.closest('.ae-track')) {
      root().querySelector('.ae-removed-list').appendChild(c);
      x.textContent = '↩'; x.title = 'Put back';
    } else {
      track().appendChild(c);
      x.textContent = '✕'; x.title = 'Remove';
    }
    commit();
  });
  let drag = null;
  document.addEventListener('pointerdown', e => {
    if (!root() || !root().contains(e.target) || e.target.closest('.ae-x') || e.target.closest('.ae-panel')) return;
    const c = e.target.closest('.ae-card');
    if (!c) return;
    if (!c.closest('.ae-track')) { select(c); return; }
    drag = {c, mode: e.target.closest('.ae-handle') ? 'trim' : 'move', x0: e.clientX, y0: e.clientY,
            d0: parseFloat(c.dataset.dur), moved: false, id: e.pointerId};
    e.preventDefault();
  });
  document.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
    if (drag.mode === 'trim') {
      let d = Math.max(MIN, drag.d0 + dx / PPS);
      const mx = parseFloat(drag.c.dataset.max);
      if (mx > 0) d = Math.min(d, mx);
      drag.c.dataset.dur = d.toFixed(2);
      setW(drag.c);
      drag.moved = true;
      return;
    }
    if (!drag.moved) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      drag.moved = true;
      const r = drag.c.getBoundingClientRect();
      drag.ox = e.clientX - r.left; drag.oy = e.clientY - r.top;
      drag.place = document.createElement('div');
      drag.place.className = 'ae-place';
      drag.place.style.width = r.width + 'px';
      drag.c.after(drag.place);
      drag.c.classList.add('ae-ghost');
      drag.c.style.width = r.width + 'px';
    }
    drag.c.style.left = (e.clientX - drag.ox) + 'px';
    drag.c.style.top = (e.clientY - drag.oy) + 'px';
    let best = null, bd = 1e9, after = false;
    for (const k of cards()) {
      if (k === drag.c) continue;
      const r = k.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const dd = Math.hypot(e.clientX - cx, (e.clientY - cy) * 2);
      if (dd < bd) { bd = dd; best = k; after = e.clientX > cx; }
    }
    if (best) (after ? best.after(drag.place) : best.before(drag.place));
  });
  function end(e) {
    if (!drag || (e && e.pointerId !== drag.id)) return;
    const d = drag;
    drag = null;
    if (d.mode === 'move' && d.moved) {
      d.place.replaceWith(d.c);
      d.c.classList.remove('ae-ghost');
      d.c.style.left = d.c.style.top = '';
      setW(d.c);
      commit();
    } else if (d.mode === 'trim' && d.moved) {
      commit();
      if (window.__aeSel === d.c.dataset.id) select(d.c);
    } else {
      select(d.c);
    }
  }
  document.addEventListener('pointerup', end);
  document.addEventListener('pointercancel', end);
  // ---------------- ▶ quick preview player (plays the timeline as it is now, no re-make needed)
  const FX = {'black & white': 'grayscale(1) contrast(1.12)', 'sepia': 'sepia(1)', 'vintage': 'sepia(.45) contrast(1.05) saturate(.8)',
              'vivid': 'saturate(1.5) contrast(1.08)', 'warm': 'sepia(.25) saturate(1.25) hue-rotate(-8deg)',
              'cool': 'saturate(.9) hue-rotate(12deg) brightness(1.03)', 'glow': 'brightness(1.12) contrast(.95) saturate(1.1)'};
  const ANIM = {'zoom in': 'ae-zin', 'zoom out': 'ae-zout', 'punch zoom': 'ae-punch .5s ease-out', 'shake': 'ae-shake .25s infinite',
                'flash': 'ae-flash .4s', 'blur in': 'ae-blurin .45s', 'glitch': 'ae-glitch .5s infinite'};
  let P = null;
  function pv() { try { return JSON.parse(root().dataset.preview || '{}'); } catch (e) { return {}; } }
  function stopPreview() {
    if (!P) return;
    clearTimeout(P.timer); cancelAnimationFrame(P.raf);
    root() && root().querySelectorAll('.ae-layer video').forEach(v => v.pause());
    if (P.audio) P.audio.pause();
    root() && root().querySelectorAll('.ae-card.ae-now').forEach(k => k.classList.remove('ae-now'));
    const b = root() && root().querySelector('.ae-play'); if (b) b.textContent = '▶ Play';
    const big = root() && root().querySelector('.ae-pbig'); if (big) big.style.display = 'flex';
    P = null;
  }
  function sizeStage() {
    const o = pv(), st = root().querySelector('.ae-stage');
    const w = o.w || 16, h = o.h || 9, maxH = Math.min(window.innerHeight * 0.55, 480);
    let width = Math.min(st.parentElement.clientWidth, maxH * w / h);
    st.style.width = width + 'px'; st.style.height = (width * h / w) + 'px';
  }
  function showText(c, o) {
    const t = root().querySelector('.ae-ptext'), st = root().querySelector('.ae-stage');
    const txt = (c.dataset.text || '').trim();
    if (!txt) { t.innerHTML = ''; return; }
    const look = (c.dataset.tlook && c.dataset.tlook !== 'default') ? c.dataset.tlook : (o.text_look || 'pill');
    const col = (c.dataset.tcolor) || o.text_color || (look === 'pill' ? '#141414' : '#ffffff');
    const pos = (c.dataset.tpos && c.dataset.tpos !== 'default') ? c.dataset.tpos : (o.text_pos || 'top');
    const sz = (c.dataset.tsize && c.dataset.tsize !== 'default') ? c.dataset.tsize : (o.text_size || 'medium');
    const base = Math.min(st.clientWidth, st.clientHeight) * 0.07 * ({small: .75, medium: 1, large: 1.35}[sz] || 1);
    t.style.fontSize = base + 'px';
    t.style.top = pos === 'top' ? '10%' : (pos === 'middle' ? '42%' : 'auto');
    t.style.bottom = pos === 'bottom' ? '18%' : 'auto';
    let css = `color:${col};`;
    if (look === 'pill') css += `background:${(parseInt(col.slice(1, 3), 16) + parseInt(col.slice(3, 5), 16) + parseInt(col.slice(5, 7), 16)) / 3 < 140 ? 'rgba(255,255,255,.92)' : 'rgba(20,20,20,.82)'};`;
    else if (look === 'colour box') css = `color:#fff;background:${col};`;
    else if (look === 'neon') css = `color:#fff;text-shadow:0 0 6px ${col},0 0 14px ${col},0 0 24px ${col};`;
    else if (look === 'shadow') css += 'text-shadow:3px 3px 4px rgba(0,0,0,.7);';
    else css += '-webkit-text-stroke: .06em #000; paint-order: stroke fill;' + (look === 'handwriting' ? "font-family:'Segoe Print','Comic Sans MS',cursive;" : "font-family:Impact,'Arial Black',sans-serif;");
    t.innerHTML = `<span style="${css}">${esc(txt)}</span>`;
  }
  function playShot(i) {
    const list = cards();
    if (!P) return;
    if (i >= list.length) { stopPreview(); return; }
    const c = list[i], o = pv();
    P.i = i;
    root().querySelectorAll('.ae-card.ae-now').forEach(k => k.classList.remove('ae-now'));
    c.classList.add('ae-now');
    const layers = root().querySelectorAll('.ae-layer');
    const L = layers[i % 2], other = layers[(i + 1) % 2];
    const dur = parseFloat(c.dataset.dur) || 3, sp = parseFloat(c.dataset.speed) || 1;
    const eff = c.dataset.effect || 'none';
    let el;
    if (c.dataset.kind === 'video') {
      el = document.createElement('video');
      el.src = c.dataset.src; el.playsInline = true; el.preload = 'auto';
      el.muted = !!o.mute || c.dataset.mute === 'off';
      el.volume = 1;
      el.addEventListener('loadedmetadata', () => { try { el.currentTime = parseFloat(c.dataset.start) || 0; } catch (e) {} el.playbackRate = sp; el.play().catch(() => { el.muted = true; el.play(); }); }, {once: true});
    } else {
      el = document.createElement('img');
      el.src = c.dataset.src;
    }
    el.style.filter = FX[eff] || '';
    el.style.transform = eff === 'mirror' ? 'scaleX(-1)' : '';
    const an = ANIM[eff] || (c.dataset.kind === 'photo' ? (i % 2 ? 'ae-zout' : 'ae-zin') : '');
    if (an) el.style.animation = an.includes(' ') ? an : `${an} ${dur}s linear forwards`;
    L.innerHTML = ''; L.appendChild(el);
    L.classList.toggle('fit', false);
    L.classList.add('on'); other.classList.remove('on');
    setTimeout(() => other.querySelectorAll('video').forEach(v => v.pause()), 400);
    showText(c, o);
    P.timer = setTimeout(() => playShot(i + 1), dur * 1000);
  }
  function startPreview() {
    stopPreview();
    if (!cards().length) return;
    sizeStage();
    const o = pv();
    P = {i: 0, t0: performance.now()};
    root().querySelector('.ae-pbig').style.display = 'none';
    root().querySelector('.ae-play').textContent = '■ Stop';
    if (o.music) {
      P.audio = new Audio(o.music);
      P.audio.volume = Math.max(0, Math.min(1, o.music_volume == null ? 0.4 : o.music_volume)) ;
      P.audio.play().catch(() => {});
    }
    const tick = () => {
      if (!P) return;
      const s = (performance.now() - P.t0) / 1000;
      const e = root().querySelector('.ae-ptime');
      if (e) e.textContent = Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
      P.raf = requestAnimationFrame(tick);
    };
    tick();
    playShot(0);
  }
  document.addEventListener('click', e => {
    if (!root() || !root().contains(e.target)) return;
    if (e.target.closest('.ae-play') || e.target.closest('.ae-stage')) {
      e.preventDefault();
      P ? stopPreview() : startPreview();
    }
  });
  new MutationObserver(() => { const st = root() && root().querySelector('.ae-stage'); if (st && !st.style.width) sizeStage(); })
    .observe(document.body, {childList: true, subtree: true});
  window.addEventListener('resize', () => root() && root().querySelector('.ae-stage') && sizeStage());
  // Mute box and music volume in the Edit tab change the preview straight away
  function setPv(k, v) {
    if (!root()) return;
    const o = pv(); o[k] = v; root().dataset.preview = JSON.stringify(o);
    if (k === 'mute') root().querySelectorAll('.ae-layer video').forEach(el => {
      const c = cards()[P ? P.i : -1];
      el.muted = v || (c && c.dataset.mute === 'off');
    });
    if (k === 'music_volume' && P && P.audio) P.audio.volume = Math.max(0, Math.min(1, v));
  }
  function watchControls(e) {
    const m = e.target.closest('#ae-mute input[type=checkbox]');
    if (m) setPv('mute', m.checked);
    const vol = e.target.closest('#ae-mvol input');
    if (vol) setPv('music_volume', (parseFloat(vol.value) || 0) / 100);
  }
  document.addEventListener('change', watchControls);
  document.addEventListener('input', watchControls);
  // edits in the panel
  document.addEventListener('change', e => {
    const f = e.target.closest('#ae-tl .ae-panel [data-k]');
    if (!f) return;
    const c = root().querySelector(`.ae-card[data-id="${window.__aeSel}"]`);
    if (!c) return;
    let v = f.value;
    if (f.dataset.k === 'dur') v = Math.max(MIN, parseFloat(v) || MIN).toFixed(2);
    if (f.dataset.k === 'start') v = Math.max(0, parseFloat(v) || 0).toFixed(2);
    c.dataset[f.dataset.k] = v;
    if (f.dataset.k === 'dur') setW(c);
    if (f.dataset.k === 'text') c.querySelector('.ae-txt').hidden = !v.trim();
    commit();
    if (f.dataset.k !== 'text') select(c);
  });
})();
</script>
"""

"""
AutoEdit updater. Runs automatically every time you start AutoEdit.
  * Online: checks the update source in update.json and installs new versions,
    new styles, effects and music moods.
  * Offline (or no update source yet): does nothing and AutoEdit starts normally.
Your own files in music/, fonts/ and output/ are never touched.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEEP = {"output", "music", "fonts", ".venv", "update.json", "__pycache__"}


def _ctx():
    try:
        import os
        import ssl
        import certifi
        return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    except Exception:
        return None


def _get(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout, context=_ctx()) as r:
        return r.read()


def _ver(s):
    return tuple(int(x) for x in s.strip().split(".") if x.isdigit())


def check_and_update(quiet=False):
    say = (lambda *a: None) if quiet else print
    try:
        cfg = json.loads((HERE / "update.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    if not cfg.get("auto_update", True) or not cfg.get("source"):
        return False
    repo = cfg["source"].rstrip("/").replace("https://github.com/", "")
    branch = cfg.get("branch", "main")
    local = (HERE / "version.txt").read_text().strip() if (HERE / "version.txt").exists() else "0"
    try:
        remote = _get(f"https://raw.githubusercontent.com/{repo}/{branch}/version.txt").decode().strip()
    except Exception:
        say("Update check skipped (offline or no update source yet). Starting normally.")
        return False
    if _ver(remote) <= _ver(local):
        say(f"AutoEdit is up to date (version {local}).")
        return False
    say(f"New version {remote} found (you have {local}). Updating...")
    try:
        data = _get(f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}", timeout=60)
    except Exception as e:
        say(f"Could not download the update ({e}). Starting the current version.")
        return False
    old_req = (HERE / "requirements.txt").read_text() if (HERE / "requirements.txt").exists() else ""
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if not roots or not (roots[0] / "autoedit.py").exists():
            say("Update package looks wrong; skipped.")
            return False
        root = roots[0]
        for item in root.iterdir():
            dest = HERE / item.name
            if item.name in KEEP:
                if item.is_dir():  # add new bundled files (e.g. README) but never overwrite yours
                    for f in item.rglob("*"):
                        t = dest / f.relative_to(item)
                        if f.is_file() and not t.exists():
                            t.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(f, t)
                continue
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    if (HERE / "requirements.txt").read_text() != old_req:
        say("Installing new components...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")])
    say(f"Updated to version {remote}.")
    return True


if __name__ == "__main__":
    check_and_update()

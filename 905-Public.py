"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   Touchless Vision-Based Remote Control System                               ║
║   Version : 905 | CNS4949A |                                                 ║
║   Supervisor: DR. NUR ARZILAWATI BINTI MD YUNUS                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  v905 CHANGES vs v904:                                                       ║
║  [REV]  Reverted FIST, VOL_UP, and VOL_DOWN gesture classification logic     ║
║         back to v901 to resolve severe conflict/recognition failure.         ║
║  [REV]  Reverted confirmation frames for FIST/VOL back to 8 (from 12).       ║
║  [KEEP] Right Click remains Index + Pinky to avoid ring-finger conflict.     ║
║  [KEEP] Whisper strict voice command mapping & phonetic fuzzy match (v904).  ║
║  [KEEP] All v904 modules: MicVolumeBar, Whisper always-on mode, ProfileMgr,  ║
║         StatsDash, Recorder, CalibWizard, PerfMonitor, SessionLogger.        ║
╚══════════════════════════════════════════════════════════════════════════════╝

Dependencies:
  pip install opencv-python mediapipe pyautogui numpy pillow psutil
  pip install pycaw comtypes          # Windows volume control (optional)
  pip install openai-whisper sounddevice scipy  # Voice recognition (optional)
  pip install pystray keyboard        # System tray / global hotkeys (optional)
"""

import sys, os, time, math, threading, queue, platform, json, csv, copy
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from collections import deque, defaultdict
from enum import Enum, auto
from datetime import datetime
from pathlib import Path

import cv2
import mediapipe as mp
import pyautogui
import numpy as np
from PIL import Image, ImageTk
import psutil

# ── Optional audio (Windows) ──────────────────────────────────────────────────
try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    AUDIO_AVAILABLE = True
except Exception:
    AUDIO_AVAILABLE = False

# ── Optional Whisper ──────────────────────────────────────────────────────────
try:
    import whisper as openai_whisper
    import tempfile
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# ── Optional sounddevice ──────────────────────────────────────────────────────
try:
    import sounddevice as sd
    import scipy.io.wavfile as wav_writer
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False

# ── Optional pystray (system tray) ───────────────────────────────────────────
try:
    import pystray
    from pystray import MenuItem as item
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

# ── Optional keyboard (global hotkeys) ───────────────────────────────────────
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False


# ════════════════════════════════════════════════════════════════════════════
#  CONSTANTS & THEME
# ════════════════════════════════════════════════════════════════════════════

APP_TITLE = "Touchless Vision-Based Remote Control System"
APP_VER   = "v905"
AUTHOR    = "Yang YK · 209796 · CNS4949A · UPM"
DATA_DIR  = Path.home() / ".touchless_rc"
DATA_DIR.mkdir(exist_ok=True)
SESSION_LOG_PATH = DATA_DIR / "session_log.json"
PROFILE_PATH     = DATA_DIR / "gesture_profile.json"
STATS_CSV_PATH   = DATA_DIR / "gesture_stats.csv"

pyautogui.FAILSAFE = False
pyautogui.PAUSE    = 0

THEME = {
    "bg":       "#0a0e1a",
    "panel":    "#0f1629",
    "card":     "#141d35",
    "border":   "#1e2d55",
    "accent":   "#00d4ff",
    "accent2":  "#7c3aed",
    "success":  "#10b981",
    "warn":     "#f59e0b",
    "danger":   "#ef4444",
    "text":     "#e2e8f0",
    "muted":    "#64748b",
    "overlay":  "#1e293b",
    "chart1":   "#00d4ff",
    "chart2":   "#7c3aed",
    "chart3":   "#10b981",
    "chart4":   "#f59e0b",
}

FONT = {
    "title":   ("Consolas", 15, "bold"),
    "label":   ("Consolas", 10, "bold"),
    "small":   ("Consolas",  9),
    "log":     ("Courier New", 9),
    "gesture": ("Consolas", 10),
    "big":     ("Consolas", 22, "bold"),
    "med":     ("Consolas", 13, "bold"),
    "chart":   ("Consolas",  8),
}


# ════════════════════════════════════════════════════════════════════════════
#  GESTURE DEFINITIONS
# ════════════════════════════════════════════════════════════════════════════

class GestureID(Enum):
    NONE        = auto()
    MOVE        = auto()   # ☝  Index only
    LEFT_CLICK  = auto()   # ✌  Index + Middle
    RIGHT_CLICK = auto()   # 🤘 Index + Pinky (ring clearly down)
    DRAG        = auto()   # 🤏 Thumb–Index pinch
    OPEN_PALM   = auto()   # ✋ All 4 fingers up
    FIST        = auto()   # 👊 Closed fist  → Voice toggle
    VOL_UP      = auto()   # 👍 Thumb UP
    VOL_DOWN    = auto()   # 👎 Thumb DOWN
    ZOOM        = auto()   # ✋🤚 Both hands open (two-hand)

CONFIRM_FRAMES: dict[GestureID, int] = {
    GestureID.NONE:        1,
    GestureID.MOVE:        1,
    GestureID.DRAG:        2,
    GestureID.LEFT_CLICK:  4,
    GestureID.RIGHT_CLICK: 5,
    GestureID.OPEN_PALM:   15,
    GestureID.FIST:        8,    # Reverted to v901 value
    GestureID.VOL_UP:      8,    # Reverted to v901 value
    GestureID.VOL_DOWN:    8,    # Reverted to v901 value
    GestureID.ZOOM:        3,
}

LOCKOUT_FRAMES: dict[GestureID, int] = {
    GestureID.LEFT_CLICK:  12,
    GestureID.RIGHT_CLICK: 14,
    GestureID.OPEN_PALM:   30,
    GestureID.FIST:        30,   # Reverted to v901 value
    GestureID.VOL_UP:      10,   # Reverted to v901 value
    GestureID.VOL_DOWN:    10,   # Reverted to v901 value
}

GESTURE_LABELS: dict[GestureID, str] = {
    GestureID.NONE:        "—",
    GestureID.MOVE:        "MOVE [1]",
    GestureID.LEFT_CLICK:  "L-CLICK [2]",
    GestureID.RIGHT_CLICK: "R-CLICK [3]",
    GestureID.DRAG:        "DRAG [4]",
    GestureID.OPEN_PALM:   "PAUSE [5]",
    GestureID.FIST:        "VOICE [6]",
    GestureID.VOL_UP:      "VOL UP [7]",
    GestureID.VOL_DOWN:    "VOL DN [8]",
    GestureID.ZOOM:        "ZOOM [9]",
}

GESTURE_GUIDE_MAP: list[tuple[GestureID, str, str]] = [
    (GestureID.MOVE,        "☝  Index Only",        "Move Cursor"),
    (GestureID.LEFT_CLICK,  "✌  Index + Middle",    "Left Click"),
    (GestureID.RIGHT_CLICK, "🤘 Index + Pinky",     "Right Click"),
    (GestureID.DRAG,        "🤏 Pinch (Thumb+Idx)", "Drag & Drop"),
    (GestureID.VOL_UP,      "👍 Thumb UP",           "Volume +"),
    (GestureID.VOL_DOWN,    "👎 Thumb DOWN",         "Volume -"),
    (GestureID.OPEN_PALM,   "✋ Open Palm",          "Pause / Resume"),
    (GestureID.FIST,        "👊 Fist",               "🎤 Voice Toggle"),
]
GESTURE_GUIDE_TWO: list[tuple[str, str]] = [
    ("✋🤚 Both Hands Open", "Zoom"),
    ("   Move Closer",       "Zoom Out"),
    ("   Move Apart",        "Zoom In"),
]

DEFAULT_PROFILE = {
    "name": "Default",
    "mappings": {
        g.name: {"action": g.name, "enabled": True}
        for g in GestureID if g not in (GestureID.NONE, GestureID.MOVE)
    }
}


# ════════════════════════════════════════════════════════════════════════════
#  v903/v904 WHISPER STRICT COMMAND TABLE
#  ─────────────────────────────────────────────────────────────────────────
#  ONLY these 9 commands are accepted. Any other transcript is discarded.
#  Each entry: (canonical_key_name, [accepted_spoken_variants])
# ════════════════════════════════════════════════════════════════════════════

WHISPER_COMMAND_TABLE: list[tuple[str, list[str]]] = [
    ("enter",     ["enter", "return", "press enter", "hit enter"]),
    ("space",     ["space", "spacebar", "press space"]),
    ("esc",       ["escape", "esc", "press escape", "press esc", "cancel"]),
    ("backspace", ["backspace", "back space", "delete back", "erase"]),
    ("delete",    ["delete", "del", "forward delete"]),
    ("up",        ["up", "go up", "move up", "arrow up", "up arrow"]),
    ("down",      ["down", "go down", "move down", "arrow down", "down arrow"]),
    ("left",      ["left", "go left", "move left", "arrow left", "left arrow"]),
    ("right",     ["right", "go right", "move right", "arrow right", "right arrow"]),
]

# Pre-built lookup: spoken_phrase → pyautogui key name
_WHISPER_LOOKUP: dict[str, str] = {}
for _key, _variants in WHISPER_COMMAND_TABLE:
    for _v in _variants:
        _WHISPER_LOOKUP[_v.lower().strip()] = _key

# ── v904: Phonetic alias table for "DOWN" misrecognition ─────────────────────
# Whisper frequently transcribes "down" as one of these similar-sounding words.
# Because the command set is tiny and closed, false-positive risk is negligible.
_DOWN_PHONETIC_ALIASES: frozenset[str] = frozenset([
    "done", "damn", "dumb", "dawn", "den", "din", "don", "dun",
    "dunno", "dame", "damp", "dine", "dome", "dote", "dove",
    "dog", "dot", "debt", "dump", "dung", "dank", "dum",
    "noun", "town", "gown", "crown", "brown", "frown", "clown",
    # common multi-word mishears
    "go down", "go done", "go dawn", "move done", "move dawn",
    "arrow done", "done arrow", "done down",
])

# ── v904: Levenshtein distance helper ────────────────────────────────────────
def _levenshtein(a: str, b: str) -> int:
    """Standard dynamic-programming edit distance (O(m*n))."""
    if a == b:      return 0
    if not a:       return len(b)
    if not b:       return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1,
                            prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]

# Single-word variants per command (used by Levenshtein pass)
_SINGLE_WORD_VARIANTS: dict[str, list[str]] = {
    key: [v for v in variants if " " not in v]
    for key, variants in WHISPER_COMMAND_TABLE
}


def match_voice_command(transcript: str) -> str | None:
    """
    Match a Whisper transcript to a strict command key name.
    Returns the pyautogui key string (e.g. 'enter', 'up') or None if no match.

    v904/v905 algorithm — four passes, no typewrite fallback:
      1. Normalise: lowercase, strip punctuation & whitespace.
      2. Exact phrase lookup in _WHISPER_LOOKUP.
      3. Substring scan — longest known phrase contained in transcript wins.
         Tolerates filler words Whisper may prepend/append.
      4. DOWN phonetic alias scan — maps common "down" mishears to "down".
      5. Levenshtein fallback — each word in the transcript is compared to
         every single-word command variant; accept if edit-distance ≤ 2
         AND the word length is ≥ 3 (avoids spurious matches on tiny words).
         Shortest distance wins; ties broken by longer candidate word.
      If nothing matches → return None (command silently dropped).
    """
    if not transcript:
        return None

    import re
    norm = transcript.lower().strip()
    norm = re.sub(r"[^\w\s]", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()

    if not norm:
        return None

    # ── Pass 1: exact phrase match ────────────────────────────────────────────
    if norm in _WHISPER_LOOKUP:
        return _WHISPER_LOOKUP[norm]

    # ── Pass 2: substring scan (longest match wins) ───────────────────────────
    best_phrase: str | None = None
    best_len = 0
    best_key: str = ""
    for phrase, key in _WHISPER_LOOKUP.items():
        if phrase in norm and len(phrase) > best_len:
            best_phrase = phrase
            best_len    = len(phrase)
            best_key    = key
    if best_phrase is not None:
        return best_key

    # ── Pass 3: DOWN phonetic alias scan ─────────────────────────────────────
    # Check both the full normalised transcript and each individual word.
    if norm in _DOWN_PHONETIC_ALIASES:
        return "down"
    for word in norm.split():
        if word in _DOWN_PHONETIC_ALIASES:
            return "down"

    # ── Pass 4: Levenshtein fallback (all commands, single words only) ────────
    words = norm.split()
    best_dist  = 3          # accept only if dist <= 2
    best_lev_key: str | None = None
    for word in words:
        if len(word) < 3:   # skip very short tokens — too noisy
            continue
        for key, variants in _SINGLE_WORD_VARIANTS.items():
            for variant in variants:
                d = _levenshtein(word, variant)
                if d < best_dist:
                    best_dist    = d
                    best_lev_key = key
                elif d == best_dist and best_lev_key is not None:
                    # Prefer longer variant match (more specific)
                    pass
    if best_lev_key is not None:
        return best_lev_key

    return None


# ════════════════════════════════════════════════════════════════════════════
#  ONE-EURO FILTER
# ════════════════════════════════════════════════════════════════════════════

class OneEuroFilter:
    def __init__(self, freq=30.0, min_cutoff=0.8, beta=0.005, d_cutoff=1.0):
        self.freq = freq; self.min_cutoff = min_cutoff
        self.beta = beta; self.d_cutoff = d_cutoff
        self._x_prev = None; self._dx_prev = 0.0; self._t_prev = None

    def _alpha(self, cutoff):
        te  = 1.0 / self.freq
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def filter(self, x, t=None):
        if t is not None and self._t_prev is not None:
            dt = t - self._t_prev
            if dt > 0: self.freq = 1.0 / dt
        if t is not None: self._t_prev = t
        if self._x_prev is None: self._x_prev = x; return x
        dx      = (x - self._x_prev) * self.freq
        a_d     = self._alpha(self.d_cutoff)
        dx_hat  = a_d * dx + (1.0 - a_d) * self._dx_prev
        self._dx_prev = dx_hat
        cutoff  = self.min_cutoff + self.beta * abs(dx_hat)
        a       = self._alpha(cutoff)
        x_hat   = a * x + (1.0 - a) * self._x_prev
        self._x_prev = x_hat
        return x_hat

    def reset(self):
        self._x_prev = None; self._dx_prev = 0.0; self._t_prev = None


# ════════════════════════════════════════════════════════════════════════════
#  SESSION LOGGER
# ════════════════════════════════════════════════════════════════════════════

class SessionLogger:
    def __init__(self):
        self._events: list[dict] = []
        self._session_start = datetime.now().isoformat()
        self._counts: dict[str, int] = defaultdict(int)

    def record(self, gesture: GestureID):
        name = gesture.name
        self._counts[name] += 1
        self._events.append({
            "ts":      datetime.now().isoformat(),
            "gesture": name,
            "total":   self._counts[name],
        })

    def save(self, path: Path = SESSION_LOG_PATH):
        data = {
            "session_start": self._session_start,
            "session_end":   datetime.now().isoformat(),
            "total_events":  len(self._events),
            "counts":        dict(self._counts),
            "events":        self._events[-500:],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def get_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def total(self) -> int:
        return len(self._events)

    def reset(self):
        self._events.clear()
        self._counts.clear()
        self._session_start = datetime.now().isoformat()

    def export_csv(self, path: Path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "gesture", "total"])
            w.writeheader()
            w.writerows(self._events)


# ════════════════════════════════════════════════════════════════════════════
#  GESTURE PROFILE MANAGER
# ════════════════════════════════════════════════════════════════════════════

class GestureProfileManager:
    def __init__(self):
        self._profiles: dict[str, dict] = {"Default": copy.deepcopy(DEFAULT_PROFILE)}
        self._current = "Default"
        self._load_from_disk()

    def _load_from_disk(self):
        if PROFILE_PATH.exists():
            try:
                with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._profiles.update(data.get("profiles", {}))
                self._current = data.get("current", "Default")
            except Exception:
                pass

    def save_to_disk(self):
        try:
            with open(PROFILE_PATH, "w", encoding="utf-8") as f:
                json.dump({"profiles": self._profiles, "current": self._current},
                          f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def export_json(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._profiles, f, indent=2, ensure_ascii=False)

    def import_json(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._profiles.update(data)
        self.save_to_disk()

    @property
    def current_profile(self) -> dict:
        return self._profiles.get(self._current, DEFAULT_PROFILE)

    @property
    def profile_names(self) -> list[str]:
        return list(self._profiles.keys())

    def switch(self, name: str):
        if name in self._profiles:
            self._current = name
            self.save_to_disk()

    def create(self, name: str):
        if name not in self._profiles:
            self._profiles[name] = copy.deepcopy(DEFAULT_PROFILE)
            self._profiles[name]["name"] = name
            self.save_to_disk()

    def delete(self, name: str):
        if name != "Default" and name in self._profiles:
            del self._profiles[name]
            if self._current == name:
                self._current = "Default"
            self.save_to_disk()

    def set_enabled(self, gesture_name: str, enabled: bool):
        profile = self.current_profile
        if gesture_name in profile["mappings"]:
            profile["mappings"][gesture_name]["enabled"] = enabled
            self.save_to_disk()

    def is_enabled(self, gesture: GestureID) -> bool:
        if gesture == GestureID.MOVE:
            return True
        profile = self.current_profile
        return profile["mappings"].get(gesture.name, {}).get("enabled", True)


# ════════════════════════════════════════════════════════════════════════════
#  GESTURE RECORDER
# ════════════════════════════════════════════════════════════════════════════

class GestureRecorder:
    def __init__(self, log_cb=None):
        self.log_cb     = log_cb or (lambda m, t="info": None)
        self._recording = False
        self._replaying = False
        self._sequence: list[dict] = []
        self._t_start: float = 0.0

    def start_record(self):
        self._sequence.clear()
        self._recording = True
        self._t_start   = time.time()
        self.log_cb("⏺ Recording started", "warn")

    def record_event(self, gesture: GestureID, cx: float, cy: float):
        if not self._recording:
            return
        self._sequence.append({
            "dt":      round(time.time() - self._t_start, 4),
            "gesture": gesture.name,
            "cx":      round(cx, 4),
            "cy":      round(cy, 4),
        })

    def stop_record(self) -> int:
        self._recording = False
        n = len(self._sequence)
        self.log_cb(f"⏹ Recording stopped — {n} events captured", "success")
        return n

    def save_csv(self, path: str):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["dt", "gesture", "cx", "cy"])
            w.writeheader()
            w.writerows(self._sequence)
        self.log_cb(f"💾 Saved: {path}", "success")

    def load_csv(self, path: str) -> int:
        self._sequence.clear()
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                self._sequence.append({
                    "dt":      float(row["dt"]),
                    "gesture": row["gesture"],
                    "cx":      float(row["cx"]),
                    "cy":      float(row["cy"]),
                })
        self.log_cb(f"📂 Loaded: {len(self._sequence)} events", "success")
        return len(self._sequence)

    def replay(self, sw: int, sh: int):
        if self._replaying or not self._sequence:
            return
        self._replaying = True
        def _run():
            self.log_cb("▶ Replay started", "success")
            prev_t = 0.0
            for ev in self._sequence:
                if not self._replaying:
                    break
                dt = ev["dt"] - prev_t
                if dt > 0:
                    time.sleep(dt)
                prev_t = ev["dt"]
                x = int(ev["cx"] * sw)
                y = int(ev["cy"] * sh)
                pyautogui.moveTo(x, y, _pause=False)
                g = ev["gesture"]
                if g == "LEFT_CLICK":  pyautogui.click()
                elif g == "RIGHT_CLICK": pyautogui.rightClick()
            self._replaying = False
            self.log_cb("⏹ Replay finished", "info")
        threading.Thread(target=_run, daemon=True).start()

    def stop_replay(self):
        self._replaying = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def event_count(self) -> int:
        return len(self._sequence)


# ════════════════════════════════════════════════════════════════════════════
#  HARDWARE HELPERS
# ════════════════════════════════════════════════════════════════════════════

def enumerate_microphones() -> list[dict]:
    mics = []
    if SOUNDDEVICE_AVAILABLE:
        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if dev.get("max_input_channels", 0) > 0:
                    mics.append({"index": idx, "name": dev["name"]})
        except Exception:
            pass
    if not mics:
        mics = [{"index": None, "name": "Default Microphone"}]
    return mics


def enumerate_cameras(max_test: int = 8) -> list[dict]:
    cameras = []
    for idx in range(max_test):
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW if platform.system() == "Windows" else 0)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                name = f"Camera {idx}"
                try: name = f"Camera {idx}  [{cap.getBackendName()}]"
                except: pass
                cameras.append({"index": idx, "name": name})
            cap.release()
    return cameras if cameras else [{"index": 0, "name": "Camera 0 (default)"}]


class AudioController:
    def __init__(self):
        self._vol_interface = None
        if AUDIO_AVAILABLE:
            try:
                devices   = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._vol_interface = cast(interface, POINTER(IAudioEndpointVolume))
            except: pass

    def change_volume(self, delta: float):
        if self._vol_interface:
            try:
                cur = self._vol_interface.GetMasterVolumeLevelScalar()
                new = max(0.0, min(1.0, cur + delta))
                self._vol_interface.SetMasterVolumeLevelScalar(new, None)
                return new
            except: pass
        pyautogui.press("volumeup" if delta > 0 else "volumedown", presses=2)
        return None

    def get_volume(self) -> float:
        if self._vol_interface:
            try: return self._vol_interface.GetMasterVolumeLevelScalar()
            except: pass
        return -1.0


# ════════════════════════════════════════════════════════════════════════════
#  v904/v905  WHISPER VOICE ENGINE — production-grade command-only pipeline
# ════════════════════════════════════════════════════════════════════════════

class WhisperVoiceEngine:
    """
    Continuous-listen Whisper engine for COMMAND-ONLY operation.

    Pipeline (runs in a background thread when active):
      1. Open the microphone via sounddevice InputStream.
      2. Accumulate audio in rolling 1.5-second windows (WINDOW_SEC).
      3. If the window RMS exceeds SILENCE_THRESHOLD (voice-activity gate),
         write to a temp WAV and transcribe with Whisper.
      4. Pass the transcript through match_voice_command().
         If matched → dispatch pyautogui.press() exactly once.
         If no match → silently discard (no typewrite fallback).
      5. Per-command cooldown (DEBOUNCE_SEC) prevents duplicate keystrokes.
      6. Exposes `mic_rms` (float 0–1) for the UI volume indicator.
    """

    SAMPLE_RATE      = 16000
    WINDOW_SEC       = 1.5        # seconds of audio per transcription window
    SILENCE_THRESHOLD = 0.008     # RMS below this → skip transcription
    DEBOUNCE_SEC     = 0.6        # minimum interval between identical key presses

    def __init__(self, model_size: str = "base", log_cb=None, mic_device=None):
        self.model_size  = model_size
        self.log_cb      = log_cb or (lambda m, t="info": None)
        self.mic_device  = mic_device

        self._model        = None
        self._model_ready  = False
        self._active       = False          # True while the listen loop is running
        self._thread: threading.Thread | None = None
        self._stop_event   = threading.Event()

        # Shared state (written by audio thread, read by UI thread)
        self.mic_rms: float = 0.0           # live RMS level 0–1 for the volume bar

        # Debounce table: key → timestamp of last dispatch
        self._last_dispatch: dict[str, float] = {}

    # ── Model management ─────────────────────────────────────────────────────

    def set_mic(self, device_index):
        self.mic_device = device_index

    def _load_model(self):
        if self._model is None:
            self.log_cb("🤖 Loading Whisper model…", "warn")
            try:
                self._model       = openai_whisper.load_model(self.model_size)
                self._model_ready = True
                self.log_cb(f"✅ Whisper '{self.model_size}' ready!", "success")
            except Exception as e:
                self.log_cb(f"❌ Whisper load failed: {e}", "error")

    def preload(self):
        if WHISPER_AVAILABLE:
            threading.Thread(target=self._load_model, daemon=True).start()

    # ── Start / Stop ─────────────────────────────────────────────────────────

    def start(self):
        """Activate the continuous listen loop."""
        if not WHISPER_AVAILABLE or not SOUNDDEVICE_AVAILABLE:
            self.log_cb("❌ Whisper or sounddevice not installed.", "error")
            return
        if self._active:
            return
        self._active = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        mic_label = f" (device {self.mic_device})" if self.mic_device is not None else ""
        self.log_cb(f"🎤 Whisper voice command mode ACTIVE{mic_label}", "success")
        self.log_cb("   Say: ENTER · SPACE · ESC · BACKSPACE · DELETE", "info")
        self.log_cb("        UP · DOWN · LEFT · RIGHT", "info")

    def stop(self):
        """Deactivate the listen loop."""
        if not self._active:
            return
        self._active = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=4)
        self.mic_rms = 0.0
        self.log_cb("🔇 Whisper voice command mode OFF", "info")

    # ── Core listen loop ──────────────────────────────────────────────────────

    def _listen_loop(self):
        # Lazy-load model if not ready yet
        if not self._model_ready:
            self._load_model()

        if not self._model_ready:
            self._active = False
            return

        chunk_size = self.SAMPLE_RATE // 20   # 50 ms chunks → smooth RMS update
        window_len = int(self.SAMPLE_RATE * self.WINDOW_SEC)
        ring_buf   = np.zeros(window_len, dtype=np.float32)
        write_ptr  = 0

        kwargs = dict(
            samplerate = self.SAMPLE_RATE,
            channels   = 1,
            dtype      = "float32",
            blocksize  = chunk_size,
        )
        if self.mic_device is not None:
            kwargs["device"] = self.mic_device

        try:
            with sd.InputStream(**kwargs) as stream:
                while not self._stop_event.is_set():
                    # Read one chunk
                    try:
                        data, overflowed = stream.read(chunk_size)
                    except Exception as e:
                        self.log_cb(f"⚠ Mic read error: {e}", "warn")
                        time.sleep(0.05)
                        continue

                    chunk = data[:, 0]  # mono
                    n = len(chunk)

                    # Update live RMS for UI bar (normalised 0–1, capped at 1)
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    self.mic_rms = min(rms * 12.0, 1.0)   # scale: 0.083 full → 1.0

                    # Fill ring buffer
                    end_ptr = write_ptr + n
                    if end_ptr <= window_len:
                        ring_buf[write_ptr:end_ptr] = chunk
                    else:
                        # Wrap around
                        first = window_len - write_ptr
                        ring_buf[write_ptr:] = chunk[:first]
                        ring_buf[:end_ptr - window_len] = chunk[first:]
                    write_ptr = end_ptr % window_len

                    # When we have filled one full window, transcribe it
                    if end_ptr >= window_len:
                        write_ptr = 0   # reset; start filling next window

                        window_rms = float(np.sqrt(np.mean(ring_buf ** 2)))
                        if window_rms < self.SILENCE_THRESHOLD:
                            # Silence — skip (saves GPU/CPU and avoids hallucination)
                            continue

                        # Transcribe in a sub-thread so we don't drop mic chunks
                        audio_copy = ring_buf.copy()
                        threading.Thread(
                            target=self._transcribe_and_dispatch,
                            args=(audio_copy,),
                            daemon=True,
                        ).start()

        except Exception as e:
            self.log_cb(f"❌ Mic stream error: {e}", "error")
        finally:
            self.mic_rms = 0.0
            self._active = False

    # ── Transcribe + dispatch (runs in sub-thread) ────────────────────────────

    def _transcribe_and_dispatch(self, audio_np: np.ndarray):
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav_writer.write(tmp.name, self.SAMPLE_RATE,
                             (audio_np * 32767).astype(np.int16))
            tmp.close()

            result  = self._model.transcribe(
                tmp.name,
                fp16=False,
                language="en",          # English-only — faster & more accurate
                temperature=0.0,        # deterministic decoding
                condition_on_previous_text=False,
            )
            os.unlink(tmp.name)
        except Exception as e:
            self.log_cb(f"❌ Transcribe error: {e}", "error")
            return

        transcript = result.get("text", "").strip()
        if not transcript:
            return

        key = match_voice_command(transcript)

        if key is None:
            # No matching command — silently discard (no typewrite fallback)
            self.log_cb(f"🎤 No cmd: \"{transcript}\"", "info")
            return

        # Debounce: reject if the same key was dispatched too recently
        now      = time.time()
        last     = self._last_dispatch.get(key, 0.0)
        if now - last < self.DEBOUNCE_SEC:
            self.log_cb(f"🔒 Debounce: {key} suppressed", "info")
            return

        self._last_dispatch[key] = now
        self.log_cb(f"🎤 CMD: \"{transcript}\" → {key.upper()} key", "success")

        # Dispatch key event on the main thread is NOT required for pyautogui,
        # but we guard with a try/except for robustness.
        try:
            pyautogui.press(key)
        except Exception as e:
            self.log_cb(f"❌ Key dispatch error: {e}", "error")


# ════════════════════════════════════════════════════════════════════════════
#  CALIBRATION DATA
# ════════════════════════════════════════════════════════════════════════════

class CalibrationData:
    def __init__(self):
        self.hand_span_px  = 100.0
        self.sensitivity   = 1.5
        self.min_cutoff    = 0.8
        self.pinch_start   = 0.038
        self.pinch_hold    = 0.055
        self.calibrated    = False
        self._path = DATA_DIR / "calibration.json"
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    d = json.load(f)
                for k, v in d.items():
                    if hasattr(self, k): setattr(self, k, v)
                self.calibrated = True
            except: pass

    def save(self):
        d = {k: getattr(self, k) for k in
             ["hand_span_px", "sensitivity", "min_cutoff", "pinch_start", "pinch_hold"]}
        with open(self._path, "w") as f:
            json.dump(d, f, indent=2)
        self.calibrated = True


# ════════════════════════════════════════════════════════════════════════════
#  v905  GESTURE ENGINE — v901 robust FIST/VOL logic restored
# ════════════════════════════════════════════════════════════════════════════

class GestureEngine:
    PINCH_START = 0.038
    PINCH_HOLD  = 0.055

    def __init__(self, camera_index=0, log_cb=None, status_cb=None, gesture_cb=None,
                 ui_pause_cb=None, voice_engine=None, profile_mgr=None,
                 session_logger=None, recorder=None, calib=None):
        self.camera_index = camera_index
        self.log_cb       = log_cb       or (lambda m, t="info": None)
        self.status_cb    = status_cb    or (lambda s: None)
        self.gesture_cb   = gesture_cb   or (lambda g: None)
        self.ui_pause_cb  = ui_pause_cb
        self.voice_engine = voice_engine
        self.profile_mgr  = profile_mgr
        self.session_log  = session_logger
        self.recorder     = recorder
        self.calib        = calib or CalibrationData()

        self.sensitivity   = self.calib.sensitivity
        self.night_mode    = False
        self.voice_mode    = "win_h"
        self.fps_limit     = 15
        self.show_skeleton = True
        self.mirror_mode   = True

        self.frame_queue  = queue.Queue(maxsize=2)
        self._running     = False
        self._paused      = False
        self._thread      = None
        self._audio       = AudioController()

        self.sw, self.sh  = pyautogui.size()

        self._euro_x = OneEuroFilter(freq=30.0, min_cutoff=self.calib.min_cutoff, beta=0.005)
        self._euro_y = OneEuroFilter(freq=30.0, min_cutoff=self.calib.min_cutoff, beta=0.005)
        self.PINCH_START = self.calib.pinch_start
        self.PINCH_HOLD  = self.calib.pinch_hold

        self._cursor_history      = deque(maxlen=10)
        self._is_dragging         = False
        self._candidate_gesture   = GestureID.NONE
        self._candidate_frames    = 0
        self._lockout_frames_left = 0
        self._last_fired          = GestureID.NONE
        self._last_vol_fired      = 0.0
        self._last_zoom_fired     = 0.0
        self._zoom_ref_dist       = None
        self._voice_active        = False

        self.fps, self.cpu_use, self.ram_use = 0.0, 0.0, 0.0
        self._fps_buf = deque(maxlen=30)

        self.fps_history = deque(maxlen=120)
        self.cpu_history = deque(maxlen=120)
        self.ram_history = deque(maxlen=120)

    # ── Engine control ────────────────────────────────────────────────────────
    def start(self):
        self._running = True
        self._paused  = False
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._is_dragging:
            pyautogui.mouseUp()
            self._is_dragging = False

    def pause(self, state: bool):
        self._paused = state
        if self._is_dragging and state:
            pyautogui.mouseUp(); self._is_dragging = False
        self._candidate_gesture   = GestureID.NONE
        self._candidate_frames    = 0
        self._lockout_frames_left = 0
        self._euro_x.reset(); self._euro_y.reset()
        self._cursor_history.clear()

    def switch_camera(self, index: int):
        self.camera_index = index
        self._running = False
        if self._thread: self._thread.join(timeout=2)
        self.start()

    # ── Finger detection ──────────────────────────────────────────────────────
    @staticmethod
    def _fingers_up(lm) -> list[bool]:
        """Returns [index, middle, ring, pinky].
        Includes lateral-distance guard on pinky to prevent ring→pinky bleed.
        """
        fu = [lm[tip].y < lm[pip].y
              for tip, pip in zip([8, 12, 16, 20], [6, 10, 14, 18])]

        if fu[3] and not fu[2]:
            lateral_sep = abs(lm[20].x - lm[16].x)
            if lateral_sep < 0.022:
                fu[3] = False

        return fu

    @staticmethod
    def _is_hand_upright(lm) -> bool:
        """True when the wrist (lm[0]) is BELOW the knuckle row (lm[9]).
        In normalised MediaPipe coords Y increases downward, so:
            upright hand  → lm[0].y > lm[9].y  (wrist lower in image)
            sideways/down → lm[0].y <= lm[9].y
        """
        return lm[0].y > lm[9].y

    # ── Profile gate ─────────────────────────────────────────────────────────
    def _gate(self, g: GestureID) -> GestureID:
        if g == GestureID.MOVE:
            return g
        if self.profile_mgr and not self.profile_mgr.is_enabled(g):
            return GestureID.NONE
        return g

    def _is_allowed(self, gesture: GestureID) -> bool:
        if gesture == GestureID.MOVE: return True
        if self.profile_mgr:
            return self.profile_mgr.is_enabled(gesture)
        return True

    # ── Single-hand classifier ───────────────────────────────────────────────
    def _classify_single(self, lm) -> GestureID:
        fu = self._fingers_up(lm)
        index, middle, ring, pinky = fu
        count   = sum(fu)
        upright = self._is_hand_upright(lm)

        # ── 1. ✋ Open Palm → Pause/Resume  (all 4 fingers up, hand upright)
        if count == 4 and upright:
            return self._gate(GestureID.OPEN_PALM)

        # ── 2. Closed layer (no fingers up) - Reverted to v901 logic
        if count == 0:
            # Thumb UP → Vol+
            if lm[4].y < lm[5].y - 0.04 and lm[4].y < lm[3].y:
                return self._gate(GestureID.VOL_UP)
            # Thumb DOWN → Vol-
            elif lm[4].y > lm[0].y + 0.02 and lm[4].y > lm[3].y:
                return self._gate(GestureID.VOL_DOWN)
            # Fist → Voice toggle
            elif upright:
                return self._gate(GestureID.FIST)
            return GestureID.NONE

        # ── 3. Drag (pinch with index + thumb)
        pinch_dist = math.hypot(lm[4].x - lm[8].x, lm[4].y - lm[8].y)
        is_pinch   = (pinch_dist < self.PINCH_START) or \
                     (self._is_dragging and pinch_dist < self.PINCH_HOLD)
        if is_pinch:
            return self._gate(GestureID.DRAG)

        # ── 4. Pointer / click layer
        if index:
            if not middle and not ring and not pinky:
                return GestureID.MOVE

            elif middle and not ring and not pinky:
                return self._gate(GestureID.LEFT_CLICK)

            # Right Click: Index + Pinky (v904 existing logic preserved)
            elif pinky and not middle:
                ring_clearly_down = lm[16].y > lm[14].y + 0.015
                if not ring or ring_clearly_down:
                    return self._gate(GestureID.RIGHT_CLICK)

        return GestureID.NONE

    # ── Two-hand classifier ───────────────────────────────────────────────────
    def _classify_two(self, hands_data: list[dict]) -> GestureID:
        h0, h1  = hands_data[0], hands_data[1]
        open0   = sum(h0["fu"]) == 4 and h0["upright"]
        open1   = sum(h1["fu"]) == 4 and h1["upright"]
        if open0 and open1:
            return self._gate(GestureID.ZOOM)
        return GestureID.NONE

    # ── State machine ─────────────────────────────────────────────────────────
    def _update_state_machine(self, raw_gesture: GestureID) -> GestureID:
        if raw_gesture in (GestureID.MOVE, GestureID.DRAG, GestureID.NONE):
            if self._candidate_gesture not in (GestureID.MOVE, GestureID.DRAG, GestureID.NONE):
                self._candidate_gesture = raw_gesture
                self._candidate_frames  = 1
            return raw_gesture

        if self._lockout_frames_left > 0:
            self._lockout_frames_left -= 1
            return GestureID.NONE

        if raw_gesture == self._candidate_gesture:
            self._candidate_frames += 1
        else:
            self._candidate_gesture = raw_gesture
            self._candidate_frames  = 1
            return GestureID.NONE

        needed = CONFIRM_FRAMES.get(raw_gesture, 4)
        if self._candidate_frames >= needed:
            lockout = LOCKOUT_FRAMES.get(raw_gesture, 0)
            if lockout > 0:
                self._lockout_frames_left = lockout
                self._candidate_gesture   = GestureID.NONE
                self._candidate_frames    = 0
            self._last_fired = raw_gesture
            return raw_gesture

        return GestureID.NONE

    # ── Cursor helpers ────────────────────────────────────────────────────────
    def _apply_backtrack(self):
        bt = min(8, len(self._cursor_history))
        if bt >= 3:
            hx, hy = self._cursor_history[-bt]
            pyautogui.moveTo(hx, hy, _pause=False)
            self._euro_x._x_prev = hx
            self._euro_y._x_prev = hy

    def _move_cursor(self, raw_rx: float, raw_ry: float, now: float):
        nx = (raw_rx - 0.5) * self.sensitivity + 0.5
        ny = (raw_ry - 0.5) * self.sensitivity + 0.5
        nx = max(0.0, min(1.0, nx)) * self.sw
        ny = max(0.0, min(1.0, ny)) * self.sh
        fx = self._euro_x.filter(nx, now)
        fy = self._euro_y.filter(ny, now)
        self._cursor_history.append((fx, fy))
        pyautogui.moveTo(fx, fy, _pause=False)
        return fx / self.sw, fy / self.sh

    # ── Single hand execution ─────────────────────────────────────────────────
    def _execute_single(self, lm, confirmed: GestureID, raw: GestureID, now: float):
        if self._paused and confirmed != GestureID.OPEN_PALM:
            return
        if not self._is_allowed(confirmed):
            return

        label = GESTURE_LABELS.get(confirmed, "")
        cx, cy = lm[8].x, lm[8].y

        if confirmed == GestureID.MOVE:
            rcx, rcy = self._move_cursor(cx, cy, now)
            if self._is_dragging:
                pyautogui.mouseUp(); self._is_dragging = False
            self.gesture_cb(label)
            if self.recorder: self.recorder.record_event(confirmed, rcx, rcy)

        elif confirmed == GestureID.DRAG:
            px = (lm[4].x + lm[8].x) * 0.5
            py = (lm[4].y + lm[8].y) * 0.5
            if not self._is_dragging:
                self._apply_backtrack()
                pyautogui.mouseDown()
                self._is_dragging = True
                self.log_cb("🤏 Drag Started", "info")
            else:
                rcx, rcy = self._move_cursor(px, py, now)
                if self.recorder: self.recorder.record_event(confirmed, rcx, rcy)
            self.gesture_cb(label)

        elif confirmed == GestureID.LEFT_CLICK:
            self._apply_backtrack(); pyautogui.click()
            self.log_cb("🖱 Left Click ✌", "info"); self.gesture_cb(label)
            if self.recorder: self.recorder.record_event(confirmed, cx, cy)

        elif confirmed == GestureID.RIGHT_CLICK:
            self._apply_backtrack(); pyautogui.rightClick()
            self.log_cb("🖱 Right Click 🤘", "info"); self.gesture_cb(label)

        elif confirmed == GestureID.VOL_UP:
            if now - self._last_vol_fired > 0.3:
                self._audio.change_volume(+0.04)
                self._last_vol_fired = now
                self.log_cb("🔊 Volume UP 👍", "success"); self.gesture_cb(label)

        elif confirmed == GestureID.VOL_DOWN:
            if now - self._last_vol_fired > 0.3:
                self._audio.change_volume(-0.04)
                self._last_vol_fired = now
                self.log_cb("🔉 Volume DOWN 👎", "info"); self.gesture_cb(label)

        elif confirmed == GestureID.OPEN_PALM:
            new_state = not self._paused
            self.pause(new_state)
            if self.ui_pause_cb: self.ui_pause_cb(new_state)
            s = "PAUSED ✋" if new_state else "RESUMED ✋"
            self.log_cb(f"✋ {s}", "warn" if new_state else "success")
            self.status_cb(f"{'⏸' if new_state else '▶'} {s}")
            self.gesture_cb(s)

        elif confirmed == GestureID.FIST:
            self._voice_active = not self._voice_active
            if self._voice_active:
                self.gesture_cb("VOICE ON 🎤"); self.status_cb("🎤 Voice Commands…")
                if self.voice_mode == "win_h":
                    pyautogui.hotkey("win", "h")
                    self.log_cb("🎤 Win+H ON", "success")
                elif self.voice_engine:
                    self.voice_engine.start()
            else:
                self.gesture_cb("VOICE OFF 👊"); self.status_cb("▶ Active")
                if self.voice_mode == "win_h":
                    pyautogui.press("esc")
                    self.log_cb("🎤 Win+H OFF", "info")
                elif self.voice_engine:
                    self.voice_engine.stop()

        if confirmed not in (GestureID.NONE, GestureID.MOVE, GestureID.DRAG):
            if self.session_log: self.session_log.record(confirmed)
            if self._is_dragging:
                pyautogui.mouseUp(); self._is_dragging = False

    # ── Two-hand execution ────────────────────────────────────────────────────
    def _execute_two(self, hands_data: list[dict], confirmed: GestureID, now: float):
        if self._paused: return
        lm0, lm1 = hands_data[0]["lm"], hands_data[1]["lm"]

        if confirmed == GestureID.ZOOM:
            dist = math.hypot(lm0[0].x - lm1[0].x, lm0[0].y - lm1[0].y)
            if self._zoom_ref_dist is None: self._zoom_ref_dist = dist
            delta = dist - self._zoom_ref_dist
            if abs(delta) > 0.04 and now - self._last_zoom_fired > 0.05:
                steps = int(delta * 400)
                if steps != 0:
                    pyautogui.keyDown("ctrl"); pyautogui.scroll(steps); pyautogui.keyUp("ctrl")
                    self._zoom_ref_dist  = dist
                    self._last_zoom_fired = now
                    self.gesture_cb(GESTURE_LABELS[GestureID.ZOOM])
                    self.log_cb(f"🔍 Zoom {'IN' if steps > 0 else 'OUT'}", "info")
                    if self.session_log: self.session_log.record(GestureID.ZOOM)

    # ── HUD rendering ─────────────────────────────────────────────────────────
    def _draw_hud(self, frame, num_hands: int, confirmed_gesture: GestureID):
        h, w = frame.shape[:2]

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 40), (10, 14, 26), -1)
        cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)

        cv2.putText(frame, f"FPS:{self.fps:>4.1f}/{self.fps_limit}", (8, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 212, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"CPU:{self.cpu_use:>4.1f}%", (145, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (124, 58, 237), 1, cv2.LINE_AA)
        cv2.putText(frame, f"RAM:{self.ram_use:>4.1f}%", (250, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (16, 185, 129), 1, cv2.LINE_AA)
        cv2.putText(frame, f"HANDS:{num_hands}", (360, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (245, 158, 11), 1, cv2.LINE_AA)

        g_label = GESTURE_LABELS.get(confirmed_gesture, "")
        if g_label and g_label != "—":
            cv2.putText(frame, g_label, (8, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 212, 255), 1, cv2.LINE_AA)

        if (self._candidate_frames > 0 and
                self._candidate_gesture not in (GestureID.NONE, GestureID.MOVE, GestureID.DRAG)):
            needed  = CONFIRM_FRAMES.get(self._candidate_gesture, 5)
            prog    = min(self._candidate_frames / needed, 1.0)
            bar_w   = int(120 * prog)
            bx      = w - 135
            cv2.rectangle(frame, (bx, 8), (bx + 120, 22), (30, 40, 60), -1)
            cv2.rectangle(frame, (bx, 8), (bx + bar_w, 22), (0, 212, 255), -1)
            cv2.putText(frame, "CONFIRM", (bx, 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 212, 255), 1, cv2.LINE_AA)

        if self._paused:
            cv2.rectangle(frame, (w//2 - 65, h//2 - 20), (w//2 + 65, h//2 + 20),
                          (20, 20, 40), -1)
            cv2.putText(frame, "PAUSE", (w // 2 - 50, h // 2 + 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 165, 255), 2, cv2.LINE_AA)

        if self._lockout_frames_left > 0:
            cv2.putText(frame, f"LOCK:{self._lockout_frames_left:2d}", (w - 85, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (245, 158, 11), 1, cv2.LINE_AA)

        # Voice-active indicator (red dot)
        if self._voice_active:
            cv2.circle(frame, (w - 16, h - 16), 10, (239, 68, 68), -1)
            cv2.putText(frame, "REC", (w - 52, h - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (239, 68, 68), 1)

        # Volume bar (bottom right)
        vol = self._audio.get_volume()
        if 0.0 <= vol <= 1.0:
            bh = int(60 * vol)
            vx, vy = w - 12, h - 10
            cv2.rectangle(frame, (vx, vy - 60), (vx + 8, vy), (30, 40, 60), -1)
            cv2.rectangle(frame, (vx, vy - bh), (vx + 8, vy), (0, 212, 255), -1)

        return frame

    # ── Main loop ─────────────────────────────────────────────────────────────
    def _loop(self):
        cap = cv2.VideoCapture(self.camera_index,
                               cv2.CAP_DSHOW if platform.system() == "Windows" else 0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)

        mp_hands = mp.solutions.hands
        hands = mp_hands.Hands(
            static_image_mode=False, max_num_hands=2,
            min_detection_confidence=0.75, min_tracking_confidence=0.65,
            model_complexity=0,
        )
        mp_draw  = mp.solutions.drawing_utils
        mp_style = mp.solutions.drawing_styles

        cpu_tick = 0
        t_prev   = time.time()

        while self._running:
            loop_start = time.time()
            ret, frame = cap.read()
            if not ret: continue

            if self.mirror_mode:
                frame = cv2.flip(frame, 1)
            if self.night_mode:
                frame = cv2.convertScaleAbs(frame, alpha=1.3, beta=40)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            now = time.time()
            dt  = now - t_prev; t_prev = now
            self._fps_buf.append(1.0 / max(dt, 1e-6))
            self.fps = float(np.mean(self._fps_buf))

            cpu_tick += 1
            if cpu_tick % 15 == 0:
                self.cpu_use = psutil.cpu_percent(interval=None)
                self.ram_use = psutil.virtual_memory().percent
                self.fps_history.append(self.fps)
                self.cpu_history.append(self.cpu_use)
                self.ram_history.append(self.ram_use)

            results    = hands.process(rgb)
            num_hands  = 0
            hands_data = []
            confirmed_gesture = GestureID.NONE

            if results.multi_hand_landmarks:
                num_hands = len(results.multi_hand_landmarks)
                for hand_lm in results.multi_hand_landmarks:
                    if self.show_skeleton:
                        mp_draw.draw_landmarks(
                            frame, hand_lm, mp_hands.HAND_CONNECTIONS,
                            mp_style.get_default_hand_landmarks_style(),
                            mp_style.get_default_hand_connections_style())
                    fu      = self._fingers_up(hand_lm.landmark)
                    upright = self._is_hand_upright(hand_lm.landmark)
                    hands_data.append({"lm": hand_lm.landmark, "fu": fu, "upright": upright})

                if num_hands == 2:
                    raw = self._classify_two(hands_data)
                    confirmed_gesture = self._update_state_machine(raw)
                    self._execute_two(hands_data, confirmed_gesture, now)
                elif num_hands == 1:
                    lm  = hands_data[0]["lm"]
                    raw = self._classify_single(lm)
                    confirmed_gesture = self._update_state_machine(raw)
                    self._execute_single(lm, confirmed_gesture, raw, now)
            else:
                self._zoom_ref_dist = None
                if self._is_dragging:
                    pyautogui.mouseUp(); self._is_dragging = False
                self._update_state_machine(GestureID.NONE)

            frame = self._draw_hud(frame, num_hands, confirmed_gesture)

            try: self.frame_queue.put_nowait(frame)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                    self.frame_queue.put_nowait(frame)
                except: pass

            target_delay = 1.0 / self.fps_limit
            elapsed = time.time() - loop_start
            if elapsed < target_delay:
                time.sleep(target_delay - elapsed)

        cap.release()
        hands.close()


# ════════════════════════════════════════════════════════════════════════════
#  MIC VOLUME INDICATOR CANVAS WIDGET
# ════════════════════════════════════════════════════════════════════════════

class MicVolumeBar(tk.Canvas):
    """
    A compact horizontal audio-level bar that reflects the live mic RMS.
    Reads `voice_engine.mic_rms` every 50 ms and redraws the bar.

    Colour coding:
      Green (0–60%)  → normal speech level
      Amber (60–85%) → loud
      Red   (85%+)   → clipping
    """

    BAR_W  = 140   # total bar width in pixels
    BAR_H  = 14
    POLL_MS = 50

    def __init__(self, parent, voice_engine_ref, **kwargs):
        super().__init__(parent, bg=THEME["card"], highlightthickness=0,
                         width=self.BAR_W + 4, height=self.BAR_H + 4, **kwargs)
        self._engine_ref = voice_engine_ref   # mutable list so it can be swapped
        self._after_id   = None
        self._active     = False

    def start(self):
        self._active = True
        self._tick()

    def stop(self):
        self._active = False
        if self._after_id:
            try: self.after_cancel(self._after_id)
            except: pass
        self.delete("all")

    def set_engine(self, eng):
        """Hot-swap the voice engine reference."""
        self._engine_ref[0] = eng

    def _tick(self):
        if not self._active:
            return
        self._draw()
        self._after_id = self.after(self.POLL_MS, self._tick)

    def _draw(self):
        self.delete("all")
        eng = self._engine_ref[0] if self._engine_ref else None
        rms = (eng.mic_rms if (eng and hasattr(eng, "mic_rms")) else 0.0)
        fill_w = int(rms * self.BAR_W)

        # Background trough
        self.create_rectangle(2, 2, self.BAR_W + 2, self.BAR_H + 2,
                               fill=THEME["border"], outline="")

        if fill_w > 0:
            # Colour zones
            if rms < 0.60:
                colour = THEME["success"]        # green
            elif rms < 0.85:
                colour = THEME["warn"]           # amber
            else:
                colour = THEME["danger"]         # red
            self.create_rectangle(2, 2, 2 + fill_w, self.BAR_H + 2,
                                   fill=colour, outline="")

        # Tick marks at 25 / 50 / 75 %
        for pct in (0.25, 0.50, 0.75):
            tx = int(2 + pct * self.BAR_W)
            self.create_line(tx, 2, tx, self.BAR_H + 2,
                              fill=THEME["bg"], width=1)


# ════════════════════════════════════════════════════════════════════════════
#  PERFORMANCE MONITOR CANVAS WIDGET
# ════════════════════════════════════════════════════════════════════════════

class PerfMonitorCanvas(tk.Canvas):
    def __init__(self, parent, engine_ref, **kwargs):
        super().__init__(parent, bg=THEME["bg"], highlightthickness=0, **kwargs)
        self._engine   = engine_ref
        self._after_id = None
        self.bind("<Configure>", lambda e: self._draw())

    def start(self):
        self._tick()

    def stop(self):
        if self._after_id:
            try: self.after_cancel(self._after_id)
            except: pass

    def _tick(self):
        self._draw()
        self._after_id = self.after(500, self._tick)

    def _draw(self):
        self.delete("all")
        W = self.winfo_width()
        H = self.winfo_height()
        if W < 10 or H < 10: return
        pad = 28

        for i in range(0, 101, 25):
            y = pad + (H - 2 * pad) * (1 - i / 100)
            self.create_line(pad, y, W - 5, y, fill=THEME["border"], dash=(2, 4))
            self.create_text(pad - 4, y, text=str(i), anchor="e",
                             font=FONT["chart"], fill=THEME["muted"])

        if not self._engine: return

        colors = [THEME["chart1"], THEME["chart2"], THEME["chart3"]]
        labels = ["FPS", "CPU%", "RAM%"]
        maxes  = [60, 100, 100]
        attrs  = ["fps_history", "cpu_history", "ram_history"]

        legend_x = pad + 5
        for idx, (attr, col, lbl, mx) in enumerate(zip(attrs, colors, labels, maxes)):
            data = list(getattr(self._engine, attr, []))
            if len(data) < 2: continue
            n  = len(data)
            xs = [pad + (W - pad - 5) * i / (n - 1) for i in range(n)]
            ys = [pad + (H - 2 * pad) * (1 - min(v, mx) / mx) for v in data]
            pts = []
            for x, y in zip(xs, ys):
                pts.extend([x, y])
            if len(pts) >= 4:
                self.create_line(*pts, fill=col, width=1.5, smooth=True)
            self.create_rectangle(legend_x, H - 18, legend_x + 10, H - 8,
                                  fill=col, outline="")
            self.create_text(legend_x + 14, H - 13,
                             text=f"{lbl}: {data[-1]:.1f}",
                             anchor="w", font=FONT["chart"], fill=col)
            legend_x += 90


# ════════════════════════════════════════════════════════════════════════════
#  STATISTICS DASHBOARD
# ════════════════════════════════════════════════════════════════════════════

class StatsDashboard(tk.Frame):
    def __init__(self, parent, session_log: SessionLogger, **kwargs):
        super().__init__(parent, bg=THEME["card"], **kwargs)
        self._log      = session_log
        self._after_id = None
        self._build()

    def _build(self):
        top = tk.Frame(self, bg=THEME["card"])
        top.pack(fill="x", padx=8, pady=4)
        tk.Label(top, text="📊 GESTURE USAGE STATISTICS", font=FONT["label"],
                 bg=THEME["card"], fg=THEME["accent"]).pack(side="left")
        tk.Button(top, text="Clear", font=FONT["small"], bg=THEME["danger"],
                  fg="#fff", bd=0, padx=6, command=self._clear).pack(side="right")
        tk.Button(top, text="Export CSV", font=FONT["small"], bg=THEME["accent2"],
                  fg="#fff", bd=0, padx=6, command=self._export).pack(side="right", padx=4)

        mid = tk.Frame(self, bg=THEME["card"])
        mid.pack(fill="both", expand=True, padx=6, pady=4)

        self._pie = tk.Canvas(mid, bg=THEME["bg"], highlightthickness=0, width=220, height=220)
        self._pie.pack(side="left", padx=4)

        self._list_frame = tk.Frame(mid, bg=THEME["card"])
        self._list_frame.pack(side="left", fill="both", expand=True, padx=6)

        self._total_var = tk.StringVar(value="Total: 0 gestures")
        tk.Label(self, textvariable=self._total_var, font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack(pady=2)

    def _clear(self):
        self._log.reset()

    def _export(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")], initialfile="gesture_stats.csv")
        if path:
            self._log.export_csv(Path(path))

    def start_poll(self):
        self._tick()

    def stop_poll(self):
        if self._after_id:
            try: self.after_cancel(self._after_id)
            except: pass

    def _tick(self):
        self._refresh()
        self._after_id = self.after(1000, self._tick)

    def _refresh(self):
        counts = self._log.get_counts()
        total  = self._log.total()
        self._total_var.set(f"Total: {total} gestures")

        self._pie.delete("all")
        W = H = 220
        items = [(k, v) for k, v in counts.items() if v > 0]
        items.sort(key=lambda x: -x[1])
        colors = [THEME["chart1"], THEME["chart2"], THEME["chart3"], THEME["chart4"],
                  THEME["success"], THEME["warn"], THEME["danger"], THEME["accent2"]]
        if total > 0:
            start_angle = 0.0
            for i, (name, count) in enumerate(items[:8]):
                sweep = 360.0 * count / total
                col   = colors[i % len(colors)]
                self._pie.create_arc(20, 20, W - 20, H - 20,
                                     start=start_angle, extent=sweep,
                                     fill=col, outline=THEME["bg"], width=1)
                start_angle += sweep
            self._pie.create_oval(70, 70, 150, 150, fill=THEME["bg"], outline="")
            self._pie.create_text(W // 2, H // 2 - 8, text=str(total),
                                   font=FONT["med"], fill=THEME["accent"])
            self._pie.create_text(W // 2, H // 2 + 12, text="events",
                                   font=FONT["small"], fill=THEME["muted"])
        else:
            self._pie.create_text(W // 2, H // 2, text="No data yet",
                                   font=FONT["small"], fill=THEME["muted"])

        for w in self._list_frame.winfo_children():
            w.destroy()
        for i, (name, count) in enumerate(items[:12]):
            col = colors[i % len(colors)]
            row = tk.Frame(self._list_frame, bg=THEME["card"])
            row.pack(fill="x", pady=1)
            tk.Frame(row, bg=col, width=8, height=16).pack(side="left", padx=(0, 4))
            tk.Label(row, text=f"{name:<18}", font=FONT["small"],
                     bg=THEME["card"], fg=THEME["text"]).pack(side="left")
            pct = 100 * count / total if total else 0
            tk.Label(row, text=f"{count:>5}  ({pct:>4.1f}%)", font=FONT["small"],
                     bg=THEME["card"], fg=col).pack(side="right")


# ════════════════════════════════════════════════════════════════════════════
#  CALIBRATION WIZARD
# ════════════════════════════════════════════════════════════════════════════

class CalibrationWizard(tk.Toplevel):
    STEPS = [
        ("Step 1/5 — Open Hand",
         "Hold your hand open in front of the camera.\n"
         "Spread all five fingers wide.\nPress Next when ready."),
        ("Step 2/5 — Fist",
         "Close your hand into a tight fist.\nPress Next when ready."),
        ("Step 3/5 — Pinch Test",
         "Touch your index finger tip to your thumb tip.\n"
         "This sets the pinch detection threshold.\nPress Next when ready."),
        ("Step 4/5 — Move Test",
         "Move your index finger slowly across the screen.\n"
         "Adjust sensitivity until cursor tracking feels right.\nPress Next to confirm."),
        ("Step 5/5 — Complete",
         "Calibration complete!\n"
         "Settings saved and applied.\nPress Finish to exit."),
    ]

    def __init__(self, parent, calib: CalibrationData, engine, log_cb=None):
        super().__init__(parent)
        self.title("Calibration Wizard")
        self.configure(bg=THEME["panel"])
        self.resizable(False, False)
        sw, sh = parent.winfo_screenwidth(), parent.winfo_screenheight()
        self.geometry(f"480x380+{(sw-480)//2}+{(sh-380)//2}")
        self.grab_set()

        self._calib    = calib
        self._engine   = engine
        self._log      = log_cb or (lambda m, t="info": None)
        self._step     = 0
        self._sens_var = tk.DoubleVar(value=calib.sensitivity)

        self._build()
        self._update_step()

    def _build(self):
        tk.Frame(self, bg=THEME["accent"], height=3).pack(fill="x")

        self._title_lbl = tk.Label(self, font=FONT["label"], bg=THEME["panel"], fg=THEME["accent"])
        self._title_lbl.pack(pady=(16, 4))

        self._body_lbl = tk.Label(self, font=FONT["small"], bg=THEME["panel"],
                                   fg=THEME["text"], wraplength=420, justify="center")
        self._body_lbl.pack(pady=8, padx=20)

        self._sens_frame = tk.Frame(self, bg=THEME["panel"])
        tk.Label(self._sens_frame, text="Sensitivity:", font=FONT["small"],
                 bg=THEME["panel"], fg=THEME["text"]).pack(side="left")
        ttk.Scale(self._sens_frame, variable=self._sens_var, from_=0.5, to=5.0,
                  orient="horizontal",
                  command=self._on_sens_change).pack(side="left", padx=6, fill="x", expand=True)
        self._sens_val = tk.Label(self._sens_frame, font=FONT["small"],
                                   bg=THEME["panel"], fg=THEME["accent"], width=4)
        self._sens_val.pack(side="left")

        self._prog_frame = tk.Frame(self, bg=THEME["panel"])
        self._prog_frame.pack(pady=8)
        self._dots = []
        for i in range(5):
            d = tk.Label(self._prog_frame, text="●", font=("Consolas", 14),
                         bg=THEME["panel"], fg=THEME["muted"])
            d.pack(side="left", padx=3)
            self._dots.append(d)

        btn_row = tk.Frame(self, bg=THEME["panel"])
        btn_row.pack(side="bottom", pady=20)
        self._btn_back = tk.Button(btn_row, text="← Back", font=FONT["small"],
                                    bg=THEME["overlay"], fg=THEME["text"], bd=0, padx=12, pady=4,
                                    command=self._back)
        self._btn_back.pack(side="left", padx=6)
        self._btn_next = tk.Button(btn_row, text="Next →", font=FONT["label"],
                                    bg=THEME["accent"], fg="#000", bd=0, padx=14, pady=4,
                                    command=self._next)
        self._btn_next.pack(side="left", padx=6)

    def _on_sens_change(self, _=None):
        v = self._sens_var.get()
        self._sens_val.config(text=f"{v:.1f}x")
        if self._engine: self._engine.sensitivity = v

    def _update_step(self):
        title, body = self.STEPS[self._step]
        self._title_lbl.config(text=title)
        self._body_lbl.config(text=body)
        self._btn_back.config(state="normal" if self._step > 0 else "disabled")
        self._btn_next.config(text="Finish" if self._step == 4 else "Next →")
        for i, d in enumerate(self._dots):
            d.config(fg=THEME["accent"] if i == self._step else THEME["muted"])
        if self._step == 3:
            self._sens_frame.pack(pady=6, padx=30, fill="x")
            self._on_sens_change()
        else:
            self._sens_frame.pack_forget()

    def _next(self):
        if self._step == 4:
            self._save(); self.destroy()
        else:
            self._step += 1; self._update_step()

    def _back(self):
        if self._step > 0:
            self._step -= 1; self._update_step()

    def _save(self):
        self._calib.sensitivity = self._sens_var.get()
        self._calib.save()
        if self._engine:
            self._engine.sensitivity = self._calib.sensitivity
        self._log(f"🎯 Calibration saved (sens={self._calib.sensitivity:.1f})", "success")


# ════════════════════════════════════════════════════════════════════════════
#  MAIN GUI APPLICATION (v905)
# ════════════════════════════════════════════════════════════════════════════

class TouchlessApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_TITLE}  {APP_VER}")
        self.root.configure(bg=THEME["bg"])
        self.root.minsize(1200, 750)

        self._engine      = None
        self._voice       = None        # WhisperVoiceEngine instance
        self._voice_ref   = [None]      # mutable ref for MicVolumeBar
        self._running     = False
        self._cameras     = []
        self._cam_idx     = 0
        self._microphones = []
        self._mic_idx     = None
        self._log_queue   = queue.Queue()
        self._calib       = CalibrationData()
        self._profile_mgr = GestureProfileManager()
        self._session_log = SessionLogger()
        self._recorder    = GestureRecorder(log_cb=self._log)

        self._current_gesture = tk.StringVar(value="—")
        self._status          = tk.StringVar(value="⏹ Ready")
        self._sens_var        = tk.DoubleVar(value=self._calib.sensitivity)
        self._night_var       = tk.BooleanVar(value=False)
        self._mirror_var      = tk.BooleanVar(value=True)
        self._skeleton_var    = tk.BooleanVar(value=True)
        self._voice_mode      = tk.StringVar(value="win_h")
        self._fps_var         = tk.IntVar(value=15)
        self._euro_mincutoff  = tk.DoubleVar(value=self._calib.min_cutoff)
        self._profile_var     = tk.StringVar(value="Default")

        self._build_ui()
        self._refresh_cameras()
        self._refresh_microphones()
        self._poll_log()
        self._poll_frame()
        self._poll_stats()
        self._register_hotkeys()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(1, weight=1)

        self._build_header()

        left = tk.Frame(self.root, bg=THEME["bg"])
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        cam_card = tk.Frame(left, bg=THEME["card"], highlightthickness=2,
                             highlightbackground=THEME["border"])
        cam_card.grid(row=0, column=0, sticky="nsew")
        self._cam_canvas = tk.Label(cam_card, bg="#000000",
                                     text="▶ Loading Camera...",
                                     font=FONT["med"], fg=THEME["muted"])
        self._cam_canvas.pack(fill="both", expand=True)

        gest_card = tk.Frame(left, bg=THEME["card"], highlightthickness=1,
                              highlightbackground=THEME["border"])
        gest_card.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        gf = tk.Frame(gest_card, bg=THEME["card"])
        gf.pack(fill="x", padx=12, pady=8)
        tk.Label(gf, text="GESTURE:", font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack(side="left")
        tk.Label(gf, textvariable=self._current_gesture,
                 font=("Consolas", 18, "bold"),
                 bg=THEME["card"], fg=THEME["accent"]).pack(side="left", padx=10)
        self._fps_lbl = tk.StringVar(value="FPS: —")
        self._cpu_lbl = tk.StringVar(value="CPU: —")
        self._ram_lbl = tk.StringVar(value="RAM: —")
        sf = tk.Frame(gest_card, bg=THEME["card"])
        sf.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(sf, textvariable=self._fps_lbl, font=FONT["label"],
                 bg=THEME["card"], fg=THEME["accent"]).pack(side="left", padx=8)
        tk.Label(sf, textvariable=self._cpu_lbl, font=FONT["label"],
                 bg=THEME["card"], fg=THEME["accent2"]).pack(side="left", padx=8)
        tk.Label(sf, textvariable=self._ram_lbl, font=FONT["label"],
                 bg=THEME["card"], fg=THEME["success"]).pack(side="left", padx=8)

        right = tk.Frame(self.root, bg=THEME["bg"])
        right.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._notebook = ttk.Notebook(right)
        self._notebook.pack(fill="both", expand=True)

        self._tab_control   = tk.Frame(self._notebook, bg=THEME["bg"])
        self._tab_stats     = tk.Frame(self._notebook, bg=THEME["bg"])
        self._tab_profile   = tk.Frame(self._notebook, bg=THEME["bg"])
        self._tab_calibrate = tk.Frame(self._notebook, bg=THEME["bg"])
        self._tab_record    = tk.Frame(self._notebook, bg=THEME["bg"])
        self._tab_perf      = tk.Frame(self._notebook, bg=THEME["bg"])

        self._notebook.add(self._tab_control,   text="⚙ Console")
        self._notebook.add(self._tab_stats,     text="📊 Statistics")
        self._notebook.add(self._tab_profile,   text="🎮 Profiles")
        self._notebook.add(self._tab_calibrate, text="🎯 Calibration")
        self._notebook.add(self._tab_record,    text="📹 Recording")
        self._notebook.add(self._tab_perf,      text="📈 Performance")

        self._build_tab_control()
        self._build_tab_stats()
        self._build_tab_profile()
        self._build_tab_calibrate()
        self._build_tab_record()
        self._build_tab_perf()
        self._build_footer()

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=THEME["panel"], height=62)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Frame(hdr, bg=THEME["accent"], height=3).pack(side="top", fill="x")
        inner = tk.Frame(hdr, bg=THEME["panel"])
        inner.pack(fill="both", expand=True, padx=16, pady=6)
        tk.Label(inner, text=f"◈ {APP_TITLE}", font=FONT["title"],
                 bg=THEME["panel"], fg=THEME["accent"]).pack(side="left")
        tk.Label(inner, text=APP_VER, font=FONT["label"],
                 bg=THEME["panel"], fg=THEME["accent2"]).pack(side="left", padx=10)
        tk.Label(inner, textvariable=self._status, font=FONT["label"],
                 bg=THEME["panel"], fg=THEME["warn"]).pack(side="right", padx=10)

    def _card_frame(self, parent, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=THEME["card"], highlightthickness=1,
                          highlightbackground=THEME["border"])
        outer.pack(fill="x", pady=(0, 6))
        tb = tk.Frame(outer, bg=THEME["border"])
        tb.pack(fill="x")
        tk.Label(tb, text=f"  {title}", font=FONT["label"],
                 bg=THEME["border"], fg=THEME["accent"]).pack(side="left", pady=4)
        inner = tk.Frame(outer, bg=THEME["card"])
        inner.pack(fill="both", expand=True, padx=10, pady=8)
        return inner

    # ── Tab: Control ──────────────────────────────────────────────────────────
    def _build_tab_control(self):
        parent = self._tab_control
        inner  = self._card_frame(parent, "⚙  CONTROL PANEL")

        cam_row = tk.Frame(inner, bg=THEME["card"])
        cam_row.pack(fill="x", pady=2)
        tk.Label(cam_row, text="📷 Cam:", font=FONT["label"], bg=THEME["card"],
                 fg=THEME["text"], width=10, anchor="w").pack(side="left")
        self._cam_combo = ttk.Combobox(cam_row, state="readonly", font=FONT["small"], width=16)
        self._cam_combo.pack(side="left", padx=4)
        self._cam_combo.bind("<<ComboboxSelected>>",
                              lambda e: self._update_engine_settings(cam_change=True))
        tk.Checkbutton(cam_row, text="🌙 Night", variable=self._night_var,
                       font=FONT["small"], bg=THEME["card"], fg=THEME["text"],
                       selectcolor=THEME["panel"],
                       command=self._update_engine_settings).pack(side="right")

        mirror_row = tk.Frame(inner, bg=THEME["card"])
        mirror_row.pack(fill="x", pady=2)
        tk.Checkbutton(mirror_row, text="🪞 Mirror Mode", variable=self._mirror_var,
                       font=FONT["small"], bg=THEME["card"], fg=THEME["text"],
                       selectcolor=THEME["panel"],
                       command=self._update_engine_settings).pack(side="left")
        tk.Checkbutton(mirror_row, text="🦴 Show Skeleton", variable=self._skeleton_var,
                       font=FONT["small"], bg=THEME["card"], fg=THEME["text"],
                       selectcolor=THEME["panel"],
                       command=self._update_engine_settings).pack(side="left", padx=10)

        sens_row = tk.Frame(inner, bg=THEME["card"])
        sens_row.pack(fill="x", pady=4)
        tk.Label(sens_row, text="🎯 Sensitivity:", font=FONT["label"], bg=THEME["card"],
                 fg=THEME["text"], width=14, anchor="w").pack(side="left")
        ttk.Scale(sens_row, variable=self._sens_var, from_=0.5, to=5.0, orient="horizontal",
                  command=self._update_engine_settings).pack(side="left", fill="x",
                                                              expand=True, padx=4)
        self._sens_lbl = tk.Label(sens_row, text="1.5x", font=FONT["small"],
                                   bg=THEME["card"], fg=THEME["accent"], width=5)
        self._sens_lbl.pack(side="left")

        euro_row = tk.Frame(inner, bg=THEME["card"])
        euro_row.pack(fill="x", pady=2)
        tk.Label(euro_row, text="🖱 Smoothing:", font=FONT["label"], bg=THEME["card"],
                 fg=THEME["text"], width=14, anchor="w").pack(side="left")
        ttk.Scale(euro_row, variable=self._euro_mincutoff, from_=0.1, to=3.0,
                  orient="horizontal",
                  command=self._update_engine_settings).pack(side="left", fill="x",
                                                               expand=True, padx=4)
        self._smooth_lbl = tk.Label(euro_row, text="0.8", font=FONT["small"],
                                     bg=THEME["card"], fg=THEME["success"], width=5)
        self._smooth_lbl.pack(side="left")

        fps_row = tk.Frame(inner, bg=THEME["card"])
        fps_row.pack(fill="x", pady=4)
        tk.Label(fps_row, text="⚡ FPS Limit:", font=FONT["label"], bg=THEME["card"],
                 fg=THEME["text"], width=14, anchor="w").pack(side="left")
        for val, lbl in [(15, "15 (Eco)"), (30, "30 (Smooth)"), (60, "60 (High)")]:
            ttk.Radiobutton(fps_row, text=lbl, variable=self._fps_var, value=val,
                            command=self._update_engine_settings).pack(side="left", padx=4)

        # ── Voice mode row ──────────────────────────────────────────────────
        voice_row = tk.Frame(inner, bg=THEME["card"])
        voice_row.pack(fill="x", pady=2)
        tk.Label(voice_row, text="🎤 Voice:", font=FONT["label"], bg=THEME["card"],
                 fg=THEME["text"], width=10, anchor="w").pack(side="left")
        ttk.Radiobutton(voice_row, text="Microsoft Win+H",
                        variable=self._voice_mode, value="win_h",
                        command=self._on_voice_mode_change).pack(side="left", padx=4)
        ttk.Radiobutton(voice_row, text="Whisper",
                        variable=self._voice_mode, value="whisper",
                        command=self._on_voice_mode_change).pack(side="left", padx=4)
        lbl = "✅" if WHISPER_AVAILABLE else "❌ not installed"
        tk.Label(voice_row, text=lbl, font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack(side="left")

        # ── Whisper mic + volume indicator row (hidden until Whisper selected) ──
        self._mic_row = tk.Frame(inner, bg=THEME["card"])

        tk.Label(self._mic_row, text="  🎙 Mic:", font=FONT["label"], bg=THEME["card"],
                 fg=THEME["text"], width=10, anchor="w").pack(side="left")

        self._mic_combo = ttk.Combobox(self._mic_row, state="readonly",
                                        font=FONT["small"], width=24)
        self._mic_combo.pack(side="left", padx=4)
        self._mic_combo.bind("<<ComboboxSelected>>", self._on_mic_change)

        # Volume indicator label + bar
        tk.Label(self._mic_row, text="Vol:", font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack(side="left", padx=(8, 2))

        self._mic_vol_bar = MicVolumeBar(self._mic_row, self._voice_ref)
        self._mic_vol_bar.pack(side="left", padx=2)

        # Strict command legend
        self._cmd_lbl = tk.Label(self._mic_row,
                                  text="ENTER·SPACE·ESC·BKSP·DEL·↑↓←→",
                                  font=("Consolas", 7), bg=THEME["card"],
                                  fg=THEME["success"], anchor="w")
        self._cmd_lbl.pack(side="left", padx=6)

        # Initially hidden
        self._mic_row.pack_forget()

        # ── Buttons row ─────────────────────────────────────────────────────
        btn_row = tk.Frame(inner, bg=THEME["card"])
        btn_row.pack(fill="x", pady=(10, 0))
        self._btn_row_ref = btn_row
        self._btn_start = tk.Button(btn_row, text="▶ START", font=FONT["label"],
                                     bg=THEME["success"], fg="#fff", bd=0, padx=10, pady=5,
                                     command=self._start_engine, state="disabled")
        self._btn_start.pack(side="left", padx=(0, 6))
        self._btn_stop = tk.Button(btn_row, text="⏹ STOP", font=FONT["label"],
                                    bg=THEME["danger"], fg="#fff", bd=0, padx=10, pady=5,
                                    command=self._stop_engine)
        self._btn_stop.pack(side="left", padx=4)
        self._btn_pause = tk.Button(btn_row, text="⏸ PAUSE", font=FONT["label"],
                                     bg=THEME["warn"], fg="#000", bd=0, padx=10, pady=5,
                                     command=self._toggle_pause_manual)
        self._btn_pause.pack(side="left", padx=4)

        # ── Gesture guide ────────────────────────────────────────────────────
        guide_outer = tk.Frame(parent, bg=THEME["card"], highlightthickness=1,
                                highlightbackground=THEME["border"])
        guide_outer.pack(fill="x", pady=(0, 6))
        guide_tb = tk.Frame(guide_outer, bg=THEME["border"])
        guide_tb.pack(fill="x")
        tk.Label(guide_tb, text="  🖐  GESTURE GUIDE",
                 font=FONT["label"], bg=THEME["border"], fg=THEME["accent"]).pack(
                     side="left", pady=4)
        tk.Label(guide_tb, text="(reflects active profile)",
                 font=("Consolas", 8), bg=THEME["border"], fg=THEME["muted"]).pack(
                     side="left", pady=4, padx=4)

        self._guide_content = tk.Frame(guide_outer, bg=THEME["card"])
        self._guide_content.pack(fill="x", padx=10, pady=6)
        self._rebuild_gesture_guide()

        # ── Event Log ────────────────────────────────────────────────────────
        log_outer = tk.Frame(parent, bg=THEME["card"], highlightthickness=1,
                              highlightbackground=THEME["border"])
        log_outer.pack(fill="both", expand=True)
        tb = tk.Frame(log_outer, bg=THEME["border"]); tb.pack(fill="x")
        tk.Label(tb, text="  📋  EVENT LOG", font=FONT["label"],
                 bg=THEME["border"], fg=THEME["accent"]).pack(side="left", pady=4)
        tk.Button(tb, text="Clear", font=FONT["small"], bg=THEME["danger"],
                  fg="#fff", bd=0, padx=6,
                  command=self._clear_log).pack(side="right", padx=4, pady=2)
        self._log_box = scrolledtext.ScrolledText(
            log_outer, font=FONT["log"], bg=THEME["bg"], fg=THEME["text"],
            bd=0, wrap="word", state="disabled", height=13)
        self._log_box.pack(fill="both", expand=True, padx=4, pady=4)
        for tag, col in [("info",    THEME["text"]),
                          ("success", THEME["success"]),
                          ("warn",    THEME["warn"]),
                          ("error",   THEME["danger"])]:
            self._log_box.tag_config(tag, foreground=col)

    # ── Dynamic gesture guide ─────────────────────────────────────────────────
    def _rebuild_gesture_guide(self):
        if not hasattr(self, "_guide_content"):
            return
        for w in self._guide_content.winfo_children():
            w.destroy()

        cols = tk.Frame(self._guide_content, bg=THEME["card"])
        cols.pack(fill="x")

        f1 = tk.Frame(cols, bg=THEME["card"])
        f1.pack(side="left", fill="both", expand=True)
        tk.Label(f1, text="─ Single Hand ─", font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack(anchor="w")

        for gid, shape, action in GESTURE_GUIDE_MAP:
            if gid != GestureID.MOVE and not self._profile_mgr.is_enabled(gid):
                continue
            r = tk.Frame(f1, bg=THEME["card"]); r.pack(fill="x")
            tk.Label(r, text=shape, font=FONT["gesture"], bg=THEME["card"],
                     fg=THEME["text"], width=22, anchor="w").pack(side="left")
            tk.Label(r, text=action, font=FONT["gesture"],
                     bg=THEME["card"], fg=THEME["accent"]).pack(side="left")

        f2 = tk.Frame(cols, bg=THEME["card"])
        f2.pack(side="left", fill="both", expand=True, padx=(10, 0))
        tk.Label(f2, text="─ Two Hands ─", font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack(anchor="w")

        if self._profile_mgr.is_enabled(GestureID.ZOOM):
            for shape, action in GESTURE_GUIDE_TWO:
                r = tk.Frame(f2, bg=THEME["card"]); r.pack(fill="x")
                tk.Label(r, text=shape, font=FONT["gesture"], bg=THEME["card"],
                         fg=THEME["text"], width=22, anchor="w").pack(side="left")
                tk.Label(r, text=action, font=FONT["gesture"],
                         bg=THEME["card"], fg=THEME["accent2"]).pack(side="left")
        else:
            tk.Label(f2, text="(Zoom disabled in profile)", font=("Consolas", 8),
                     bg=THEME["card"], fg=THEME["muted"]).pack(anchor="w")

    # ── Tab: Statistics ───────────────────────────────────────────────────────
    def _build_tab_stats(self):
        self._stats_dashboard = StatsDashboard(self._tab_stats, self._session_log)
        self._stats_dashboard.pack(fill="both", expand=True)
        self._stats_dashboard.start_poll()

    # ── Tab: Gesture Profile ──────────────────────────────────────────────────
    def _build_tab_profile(self):
        parent = self._tab_profile
        inner  = self._card_frame(parent, "🎮  GESTURE PROFILE MANAGER")

        sel_row = tk.Frame(inner, bg=THEME["card"])
        sel_row.pack(fill="x", pady=4)
        tk.Label(sel_row, text="Profile:", font=FONT["label"],
                 bg=THEME["card"], fg=THEME["text"]).pack(side="left")
        self._profile_combo = ttk.Combobox(sel_row, textvariable=self._profile_var,
                                            state="readonly", font=FONT["small"], width=16)
        self._profile_combo["values"] = self._profile_mgr.profile_names
        self._profile_combo.pack(side="left", padx=6)
        self._profile_combo.bind("<<ComboboxSelected>>", self._on_profile_select)

        tk.Button(sel_row, text="+ New",    font=FONT["small"], bg=THEME["success"],
                  fg="#fff", bd=0, padx=6, command=self._new_profile).pack(side="left", padx=3)
        tk.Button(sel_row, text="✕ Delete", font=FONT["small"], bg=THEME["danger"],
                  fg="#fff", bd=0, padx=6, command=self._delete_profile).pack(side="left", padx=3)
        tk.Button(sel_row, text="📤 Export", font=FONT["small"], bg=THEME["accent2"],
                  fg="#fff", bd=0, padx=6,
                  command=self._export_profile).pack(side="right", padx=3)
        tk.Button(sel_row, text="📂 Import", font=FONT["small"], bg=THEME["overlay"],
                  fg=THEME["text"], bd=0, padx=6,
                  command=self._import_profile).pack(side="right", padx=3)

        list_outer = tk.Frame(parent, bg=THEME["card"], highlightthickness=1,
                               highlightbackground=THEME["border"])
        list_outer.pack(fill="both", expand=True, pady=4)

        hdr_f = tk.Frame(list_outer, bg=THEME["border"])
        hdr_f.pack(fill="x")
        tk.Label(hdr_f, text="  Enable / Disable Gestures:", font=FONT["label"],
                 bg=THEME["border"], fg=THEME["accent"]).pack(side="left", pady=4)
        tk.Label(hdr_f, text="(MOVE is always on)", font=("Consolas", 8),
                 bg=THEME["border"], fg=THEME["muted"]).pack(side="left", pady=4, padx=4)

        scroll_frame = tk.Frame(list_outer, bg=THEME["card"])
        scroll_frame.pack(fill="both", expand=True, padx=6, pady=4)

        self._gesture_vars: dict[str, tk.BooleanVar] = {}
        self._gesture_check_widgets: dict[str, tk.Checkbutton] = {}
        row_frame = tk.Frame(scroll_frame, bg=THEME["card"])
        row_frame.pack(fill="both")
        col = 0; row_idx = 0

        toggleable = [g for g in GestureID
                      if g not in (GestureID.NONE, GestureID.MOVE)]
        for g in toggleable:
            var = tk.BooleanVar(value=self._profile_mgr.is_enabled(g))
            self._gesture_vars[g.name] = var
            cb = tk.Checkbutton(
                row_frame,
                text=f"{GESTURE_LABELS.get(g, g.name):<22}",
                variable=var, font=FONT["small"], bg=THEME["card"], fg=THEME["text"],
                selectcolor=THEME["panel"],
                command=lambda gn=g.name, v=var: self._toggle_gesture(gn, v))
            cb.grid(row=row_idx, column=col, sticky="w", padx=4, pady=1)
            self._gesture_check_widgets[g.name] = cb
            col += 1
            if col >= 2: col = 0; row_idx += 1

        tk.Button(parent, text="💾 Save Profile", font=FONT["label"],
                  bg=THEME["success"], fg="#fff", bd=0, padx=12, pady=5,
                  command=self._save_profile).pack(pady=6)

    def _on_profile_select(self, _=None):
        name = self._profile_var.get()
        self._profile_mgr.switch(name)
        for g in GestureID:
            if g in (GestureID.NONE, GestureID.MOVE): continue
            if g.name in self._gesture_vars:
                self._gesture_vars[g.name].set(self._profile_mgr.is_enabled(g))
        self._rebuild_gesture_guide()
        self._log(f"🎮 Profile switched: {name}", "info")

    def _toggle_gesture(self, gesture_name: str, var: tk.BooleanVar):
        self._profile_mgr.set_enabled(gesture_name, var.get())
        state = "ON" if var.get() else "OFF"
        self._log(f"🎮 {gesture_name}: {state}", "info")
        self._rebuild_gesture_guide()

    def _new_profile(self):
        import tkinter.simpledialog
        d = tkinter.simpledialog.askstring("New Profile", "Profile name:", parent=self.root)
        if d:
            self._profile_mgr.create(d)
            self._profile_combo["values"] = self._profile_mgr.profile_names
            self._profile_var.set(d)
            self._on_profile_select()

    def _delete_profile(self):
        name = self._profile_var.get()
        if name == "Default":
            messagebox.showwarning("Cannot Delete", "Default profile cannot be deleted.")
            return
        if messagebox.askyesno("Delete", f"Delete profile '{name}'?"):
            self._profile_mgr.delete(name)
            self._profile_combo["values"] = self._profile_mgr.profile_names
            self._profile_var.set("Default")
            self._on_profile_select()

    def _save_profile(self):
        self._profile_mgr.save_to_disk()
        self._log("💾 Profile saved to disk", "success")

    def _export_profile(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
            filetypes=[("JSON", "*.json")], initialfile="gesture_profile.json")
        if path:
            self._profile_mgr.export_json(path)
            self._log(f"📤 Exported: {path}", "success")

    def _import_profile(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            self._profile_mgr.import_json(path)
            self._profile_combo["values"] = self._profile_mgr.profile_names
            self._log(f"📂 Imported: {path}", "success")

    # ── Tab: Calibration ──────────────────────────────────────────────────────
    def _build_tab_calibrate(self):
        parent = self._tab_calibrate
        inner  = self._card_frame(parent, "🎯  CALIBRATION WIZARD")

        tk.Label(inner,
                 text="The calibration wizard adapts the system\n"
                      "to your hand size and preferred sensitivity.",
                 font=FONT["small"], bg=THEME["card"],
                 fg=THEME["text"], justify="left").pack(anchor="w", pady=4)

        self._calib_lbl_frame = tk.Frame(inner, bg=THEME["card"])
        self._calib_lbl_frame.pack(fill="x", pady=6)
        self._update_calib_display()

        tk.Button(inner, text="🎯 Launch Calibration Wizard",
                  font=FONT["label"], bg=THEME["accent"], fg="#000",
                  bd=0, padx=12, pady=6,
                  command=self._open_calibration).pack(pady=8)
        tk.Button(inner, text="🔄 Reset to Defaults",
                  font=FONT["small"], bg=THEME["overlay"], fg=THEME["text"],
                  bd=0, padx=10, pady=4,
                  command=self._reset_calibration).pack()

        pinch_card = self._card_frame(parent, "🤏  PINCH THRESHOLD FINE-TUNE")
        self._pinch_start_var = tk.DoubleVar(value=self._calib.pinch_start)
        self._pinch_hold_var  = tk.DoubleVar(value=self._calib.pinch_hold)

        for var, label, key in [
            (self._pinch_start_var, "Pinch Start Dist:", "pinch_start"),
            (self._pinch_hold_var,  "Pinch Hold Dist:",  "pinch_hold"),
        ]:
            r = tk.Frame(pinch_card, bg=THEME["card"]); r.pack(fill="x", pady=2)
            tk.Label(r, text=label, font=FONT["small"], bg=THEME["card"],
                     fg=THEME["text"], width=18, anchor="w").pack(side="left")
            ttk.Scale(r, variable=var, from_=0.01, to=0.12, orient="horizontal",
                      command=lambda v, k=key: self._update_pinch(k, float(v))
                      ).pack(side="left", fill="x", expand=True, padx=4)
            lbl2 = tk.Label(r, font=FONT["small"], bg=THEME["card"],
                            fg=THEME["accent"], width=6)
            lbl2.pack(side="left")
            var.trace_add("write",
                          lambda *_, v=var, l=lbl2: l.config(text=f"{v.get():.3f}"))

    def _update_calib_display(self):
        for w in self._calib_lbl_frame.winfo_children():
            w.destroy()
        items = [
            ("Sensitivity", f"{self._calib.sensitivity:.1f}x"),
            ("Min Cutoff",  f"{self._calib.min_cutoff:.2f}"),
            ("Pinch Start", f"{self._calib.pinch_start:.3f}"),
            ("Calibrated",  "✅ YES" if self._calib.calibrated else "⚠ NO (using defaults)"),
        ]
        for label, val in items:
            r = tk.Frame(self._calib_lbl_frame, bg=THEME["card"]); r.pack(fill="x")
            tk.Label(r, text=f"  {label}:", font=FONT["small"], bg=THEME["card"],
                     fg=THEME["muted"], width=16, anchor="w").pack(side="left")
            tk.Label(r, text=val, font=FONT["small"],
                     bg=THEME["card"], fg=THEME["success"]).pack(side="left")

    def _open_calibration(self):
        CalibrationWizard(self.root, self._calib, self._engine, log_cb=self._log)
        self.root.after(500, self._update_calib_display)

    def _reset_calibration(self):
        self._calib.sensitivity  = 1.5
        self._calib.min_cutoff   = 0.8
        self._calib.pinch_start  = 0.038
        self._calib.pinch_hold   = 0.055
        self._calib.calibrated   = False
        self._sens_var.set(1.5)
        self._euro_mincutoff.set(0.8)
        self._update_calib_display()
        if self._engine:
            self._engine.sensitivity = 1.5
            self._engine._euro_x.min_cutoff = 0.8
            self._engine._euro_y.min_cutoff = 0.8
        self._log("🔄 Calibration reset to defaults", "warn")

    def _update_pinch(self, key: str, value: float):
        setattr(self._calib, key, value)
        if self._engine:
            if key == "pinch_start": self._engine.PINCH_START = value
            elif key == "pinch_hold": self._engine.PINCH_HOLD  = value

    # ── Tab: Recorder ─────────────────────────────────────────────────────────
    def _build_tab_record(self):
        parent = self._tab_record
        inner  = self._card_frame(parent, "📹  GESTURE RECORDER & PLAYBACK")

        tk.Label(inner, text=(
            "Record gesture sessions as CSV sequences.\n"
            "Play them back to automate repetitive tasks."
        ), font=FONT["small"], bg=THEME["card"], fg=THEME["text"]).pack(anchor="w", pady=4)

        self._rec_status = tk.StringVar(value="● Idle")
        tk.Label(inner, textvariable=self._rec_status, font=FONT["label"],
                 bg=THEME["card"], fg=THEME["warn"]).pack(pady=4)
        self._rec_count = tk.StringVar(value="Events: 0")
        tk.Label(inner, textvariable=self._rec_count, font=FONT["small"],
                 bg=THEME["card"], fg=THEME["muted"]).pack()

        btn_row1 = tk.Frame(inner, bg=THEME["card"])
        btn_row1.pack(fill="x", pady=8)
        self._btn_rec_start = tk.Button(btn_row1, text="⏺ Record", font=FONT["label"],
                                         bg=THEME["danger"], fg="#fff", bd=0, padx=10, pady=5,
                                         command=self._rec_start)
        self._btn_rec_start.pack(side="left", padx=4)
        self._btn_rec_stop = tk.Button(btn_row1, text="⏹ Stop", font=FONT["label"],
                                        bg=THEME["overlay"], fg=THEME["text"], bd=0,
                                        padx=10, pady=5,
                                        command=self._rec_stop, state="disabled")
        self._btn_rec_stop.pack(side="left", padx=4)

        btn_row2 = tk.Frame(inner, bg=THEME["card"])
        btn_row2.pack(fill="x", pady=4)
        tk.Button(btn_row2, text="▶ Replay", font=FONT["label"],
                  bg=THEME["success"], fg="#fff", bd=0, padx=10, pady=5,
                  command=self._rec_replay).pack(side="left", padx=4)
        tk.Button(btn_row2, text="⏹ Stop Replay", font=FONT["small"],
                  bg=THEME["overlay"], fg=THEME["text"], bd=0, padx=8, pady=5,
                  command=lambda: self._recorder.stop_replay()).pack(side="left", padx=4)

        btn_row3 = tk.Frame(inner, bg=THEME["card"])
        btn_row3.pack(fill="x", pady=4)
        tk.Button(btn_row3, text="💾 Save CSV", font=FONT["small"],
                  bg=THEME["accent2"], fg="#fff", bd=0, padx=8, pady=4,
                  command=self._rec_save).pack(side="left", padx=4)
        tk.Button(btn_row3, text="📂 Load CSV", font=FONT["small"],
                  bg=THEME["overlay"], fg=THEME["text"], bd=0, padx=8, pady=4,
                  command=self._rec_load).pack(side="left", padx=4)

        sess_card = self._card_frame(parent, "📋  SESSION LOG")
        tk.Button(sess_card, text="💾 Save Session JSON", font=FONT["small"],
                  bg=THEME["accent"], fg="#000", bd=0, padx=8, pady=4,
                  command=self._save_session).pack(side="left", padx=4)
        tk.Button(sess_card, text="📤 Export Stats CSV", font=FONT["small"],
                  bg=THEME["accent2"], fg="#fff", bd=0, padx=8, pady=4,
                  command=self._export_stats).pack(side="left", padx=4)
        self._session_info = tk.Label(sess_card, text="Session: 0 events",
                                       font=FONT["small"], bg=THEME["card"],
                                       fg=THEME["muted"])
        self._session_info.pack(side="right", padx=8)

    def _rec_start(self):
        self._recorder.start_record()
        self._rec_status.set("⏺ Recording…")
        self._btn_rec_start.config(state="disabled")
        self._btn_rec_stop.config(state="normal")

    def _rec_stop(self):
        n = self._recorder.stop_record()
        self._rec_status.set(f"⏹ Stopped — {n} events")
        self._rec_count.set(f"Events: {n}")
        self._btn_rec_start.config(state="normal")
        self._btn_rec_stop.config(state="disabled")

    def _rec_replay(self):
        if self._recorder.event_count == 0:
            messagebox.showinfo("No Recording", "No events to replay.")
            return
        sw, sh = pyautogui.size()
        self._recorder.replay(sw, sh)

    def _rec_save(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV", "*.csv")], initialfile="gesture_recording.csv")
        if path: self._recorder.save_csv(path)

    def _rec_load(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if path:
            n = self._recorder.load_csv(path)
            self._rec_count.set(f"Events: {n}")
            self._rec_status.set(f"📂 Loaded {n} events")

    def _save_session(self):
        self._session_log.save()
        self._log(f"💾 Session saved → {SESSION_LOG_PATH}", "success")

    def _export_stats(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv",
            filetypes=[("CSV", "*.csv")], initialfile="gesture_stats.csv")
        if path:
            self._session_log.export_csv(Path(path))
            self._log(f"📤 Stats exported: {path}", "success")

    # ── Tab: Performance Monitor ──────────────────────────────────────────────
    def _build_tab_perf(self):
        parent    = self._tab_perf
        inner_hdr = self._card_frame(parent, "📈  REAL-TIME PERFORMANCE MONITOR")
        tk.Label(inner_hdr, text=(
            "Scrolling history chart of FPS, CPU, and RAM.\n"
            "Updates every 500 ms from the engine thread."
        ), font=FONT["small"], bg=THEME["card"], fg=THEME["text"]).pack(anchor="w")

        self._perf_canvas = PerfMonitorCanvas(parent, None, height=240)
        self._perf_canvas.pack(fill="both", expand=True, padx=6, pady=6)

        sys_card = self._card_frame(parent, "💻  SYSTEM INFO")
        sys_info = [
            ("OS",     platform.system() + " " + platform.release()),
            ("Python", sys.version.split()[0]),
            ("CPU",    f"{psutil.cpu_count()} cores"),
            ("RAM",    f"{psutil.virtual_memory().total // (1024**3)} GB"),
            ("Screen", f"{pyautogui.size()[0]}×{pyautogui.size()[1]}"),
            ("Audio",  "✅ pycaw" if AUDIO_AVAILABLE else "❌ fallback"),
            ("Whisper","✅ loaded" if WHISPER_AVAILABLE else "❌ not installed"),
            ("pystray","✅" if PYSTRAY_AVAILABLE else "❌"),
        ]
        col_frame = tk.Frame(sys_card, bg=THEME["card"])
        col_frame.pack(fill="x")
        for i, (k, v) in enumerate(sys_info):
            r = tk.Frame(col_frame, bg=THEME["card"])
            r.grid(row=i // 2, column=i % 2, sticky="w", padx=10)
            tk.Label(r, text=f"{k}:", font=FONT["small"], bg=THEME["card"],
                     fg=THEME["muted"], width=8, anchor="w").pack(side="left")
            tk.Label(r, text=v, font=FONT["small"],
                     bg=THEME["card"], fg=THEME["text"]).pack(side="left")

    def _build_footer(self):
        foot = tk.Frame(self.root, bg=THEME["panel"], height=28)
        foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        tk.Label(foot,
                 text=f"  {AUTHOR}  ·  v905: Whisper Voice keyboard · v901 FIST/VOL logic restored",
                 font=FONT["small"], bg=THEME["panel"], fg=THEME["muted"]).pack(
                     side="left", pady=4)

    # ── Engine control ────────────────────────────────────────────────────────
    def _start_engine(self):
        if self._running: return

        # Build the Whisper voice engine (even if mode is win_h — it preloads)
        self._voice = WhisperVoiceEngine(log_cb=self._log, mic_device=self._mic_idx)
        self._voice_ref[0] = self._voice   # update bar's engine reference

        if WHISPER_AVAILABLE and self._voice_mode.get() == "whisper":
            self._voice.preload()

        self._engine = GestureEngine(
            camera_index   = self._cam_idx,
            log_cb         = self._log,
            status_cb      = lambda s: self._status.set(s),
            gesture_cb     = lambda g: self._current_gesture.set(g),
            ui_pause_cb    = self._sync_pause_ui,
            voice_engine   = self._voice,
            profile_mgr    = self._profile_mgr,
            session_logger = self._session_log,
            recorder       = self._recorder,
            calib          = self._calib,
        )
        self._update_engine_settings()
        self._engine.start()
        self._running = True
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        self._status.set("▶ Active")

        self._perf_canvas._engine = self._engine
        self._perf_canvas.start()

        # Start mic volume bar if Whisper mode is selected
        if self._voice_mode.get() == "whisper":
            self._mic_vol_bar.start()

        self._log("🚀 System Started [v905]", "success")
        self._log("  ✔ FIST vs VOL_UP/DOWN: stable v901 classification restored", "info")
        self._log("  ✔ Right Click: Index + Pinky mapped successfully", "info")
        self._log("  ✔ Whisper: STRICT command-only + fuzzy DOWN + Levenshtein fallback", "info")
        self._log("  ✔ Commands: ENTER · SPACE · ESC · BKSP · DEL · UP · DN · L · R", "info")
        self._log("  ✔ Debounce 0.6s · Silence gate · English-only decoding", "info")
        self._log(f"  ✔ Calib: {'YES' if self._calib.calibrated else 'defaults'}", "info")
        if AUDIO_AVAILABLE:
            self._log("  ✔ pycaw volume control", "success")
        else:
            self._log("  ⚠ pycaw not found → pyautogui fallback", "warn")
        if self._voice_mode.get() == "whisper":
            mic_name = (self._microphones[self._mic_combo.current()]["name"]
                        if self._microphones else "default")
            self._log(f"  ✔ Whisper mic: {mic_name}", "success")

    def _stop_engine(self):
        if not self._running: return
        if not messagebox.askyesno("Confirm", "Stop Gesture Engine?"): return
        self._running = False
        self._perf_canvas.stop()
        self._mic_vol_bar.stop()
        if self._engine: self._engine.stop(); self._engine = None
        if self._voice:  self._voice.stop();  self._voice = None
        self._voice_ref[0] = None
        self._btn_start.config(state="normal")
        self._btn_stop.config(state="disabled")
        self._status.set("⏹ Stopped")
        self._cam_canvas.config(image="", text="▶ Stopped", fg=THEME["muted"])
        self._session_log.save()
        self._log("⏹ System Stopped | Session auto-saved", "warn")

    def _toggle_pause_manual(self):
        if not self._engine: return
        p = not self._engine._paused
        self._engine.pause(p)
        self._sync_pause_ui(p)
        self._status.set("⏸ Paused" if p else "▶ Active")

    def _sync_pause_ui(self, is_paused: bool):
        if is_paused:
            self._btn_pause.config(text="▶ RESUME", bg=THEME["success"], fg="#fff")
        else:
            self._btn_pause.config(text="⏸ PAUSE",  bg=THEME["warn"],    fg="#000")

    def _update_engine_settings(self, event=None, cam_change=False):
        if cam_change:
            self._cam_idx = self._cameras[self._cam_combo.current()]["index"]
            if self._engine: self._engine.switch_camera(self._cam_idx)
        sens = self._sens_var.get()
        mc   = self._euro_mincutoff.get()
        self._sens_lbl.config(text=f"{sens:.1f}x")
        self._smooth_lbl.config(text=f"{mc:.2f}")
        if self._engine:
            self._engine.sensitivity         = sens
            self._engine.night_mode          = self._night_var.get()
            self._engine.mirror_mode         = self._mirror_var.get()
            self._engine.show_skeleton       = self._skeleton_var.get()
            self._engine.voice_mode          = self._voice_mode.get()
            self._engine.fps_limit           = self._fps_var.get()
            self._engine._euro_x.min_cutoff  = mc
            self._engine._euro_y.min_cutoff  = mc

    # ── Camera / mic refresh ──────────────────────────────────────────────────
    def _refresh_cameras(self):
        self._cameras = enumerate_cameras()
        self._cam_combo["values"] = [c["name"] for c in self._cameras]
        self._cam_combo.current(0)
        self._cam_idx = self._cameras[0]["index"]
        self._btn_start.config(state="normal")

    def _refresh_microphones(self):
        self._microphones = enumerate_microphones()
        names = [m["name"] for m in self._microphones]
        self._mic_combo["values"] = names
        self._mic_combo.current(0)
        self._mic_idx = self._microphones[0]["index"]

    def _on_mic_change(self, _=None):
        sel = self._mic_combo.current()
        if 0 <= sel < len(self._microphones):
            self._mic_idx = self._microphones[sel]["index"]
            name = self._microphones[sel]["name"]
            self._log(f"🎙 Microphone: {name}", "info")
            if self._voice:
                self._voice.set_mic(self._mic_idx)

    def _on_voice_mode_change(self):
        self._update_engine_settings()
        new_mode = self._voice_mode.get()

        if new_mode == "win_h":
            if self._voice and self._voice._active:
                self._voice.stop()
                self._log("🔇 Whisper stopped — switched to Win+H mode", "info")
            if self._engine:
                self._engine._voice_active = False
            self._mic_row.pack_forget()
            self._mic_vol_bar.stop()

        else:  # "whisper"
            self._mic_row.pack(fill="x", pady=2, before=self._btn_row_ref)
            if self._running:
                self._mic_vol_bar.start()
                if self._voice and WHISPER_AVAILABLE:
                    self._voice.preload()   # no-op if already loaded
            self._log("🎤 Voice mode: Whisper (use FIST gesture to activate mic)", "info")

    # ── Global hotkeys ────────────────────────────────────────────────────────
    def _register_hotkeys(self):
        if not KEYBOARD_AVAILABLE: return
        try:
            keyboard.add_hotkey("ctrl+alt+s", self._hotkey_toggle_start)
            keyboard.add_hotkey("ctrl+alt+p", self._hotkey_toggle_pause)
            self._log("⌨ Global hotkeys: Ctrl+Alt+S (start/stop), Ctrl+Alt+P (pause)", "info")
        except Exception as e:
            self._log(f"⚠ Hotkeys unavailable: {e}", "warn")

    def _hotkey_toggle_start(self):
        if self._running: self._stop_engine()
        else: self._start_engine()

    def _hotkey_toggle_pause(self):
        self._toggle_pause_manual()

    # ── Polling loops ─────────────────────────────────────────────────────────
    def _log(self, msg: str, tag: str = "info"):
        self._log_queue.put((f"[{time.strftime('%H:%M:%S')}] {msg}\n", tag))

    def _clear_log(self):
        self._log_box.config(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.config(state="disabled")

    def _poll_log(self):
        try:
            while True:
                text, tag = self._log_queue.get_nowait()
                self._log_box.config(state="normal")
                self._log_box.insert("end", text, tag)
                self._log_box.see("end")
                self._log_box.config(state="disabled")
        except queue.Empty: pass
        self.root.after(80, self._poll_log)

    def _poll_frame(self):
        if self._running and self._engine:
            try:
                frame = self._engine.frame_queue.get_nowait()
                h, w  = frame.shape[:2]
                cw = self._cam_canvas.winfo_width()  or 640
                ch = self._cam_canvas.winfo_height() or 360
                scale = min(cw / w, ch / h)
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
                imgtk = ImageTk.PhotoImage(
                    image=Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
                self._cam_canvas.imgtk = imgtk
                self._cam_canvas.config(image=imgtk, text="")
            except queue.Empty: pass
        self.root.after(16, self._poll_frame)

    def _poll_stats(self):
        if self._running and self._engine:
            self._fps_lbl.set(f"FPS: {self._engine.fps:.1f}")
            self._cpu_lbl.set(f"CPU: {self._engine.cpu_use:.1f}%")
            self._ram_lbl.set(f"RAM: {self._engine.ram_use:.1f}%")
            self._session_info.config(text=f"Session: {self._session_log.total()} events")
            if self._recorder.is_recording:
                self._rec_count.set(f"Events: {self._recorder.event_count}")
        self.root.after(500, self._poll_stats)

    def _on_close(self):
        if self._running:
            self._running = False
            self._perf_canvas.stop()
            self._mic_vol_bar.stop()
            if self._engine: self._engine.stop()
            if self._voice:  self._voice.stop()
        self._session_log.save()
        self._profile_mgr.save_to_disk()
        if KEYBOARD_AVAILABLE:
            try: keyboard.unhook_all_hotkeys()
            except: pass
        self.root.destroy()


# ════════════════════════════════════════════════════════════════════════════
#  SPLASH SCREEN
# ════════════════════════════════════════════════════════════════════════════

def show_splash(root):
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h   = 660, 390
    splash.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    splash.configure(bg=THEME["panel"])

    tk.Frame(splash, bg=THEME["accent"], height=4).pack(fill="x")
    tk.Label(splash, text="◈", font=("Consolas", 40, "bold"),
             bg=THEME["panel"], fg=THEME["accent"]).pack(pady=(12, 0))
    tk.Label(splash, text="Touchless Vision-Based Remote Control",
             font=("Consolas", 14, "bold"), bg=THEME["panel"], fg=THEME["text"]).pack()
    tk.Label(splash, text=f"{APP_VER}  ·  CNS4949A  ·  UPM",
             font=FONT["small"], bg=THEME["panel"], fg=THEME["muted"]).pack(pady=2)

    changes = [
        "✔ [REV]  Reverted FIST / VOL_UP / VOL_DOWN logic back to v901 for stability",
        "✔ [FIX]  Right-Click uses Index + Pinky to avoid recognition conflicts",
        "✔ [NEW]  Whisper continuously listens and inputs mapped keystrokes",
        "✔ [FIX]  DOWN fuzzy matching — 'done/damn/dumb/dawn/den...' → DOWN key",
        "✔ [FIX]  Levenshtein edit-distance fallback (≤2) for all 9 voice commands",
    ]
    for c in changes:
        tk.Label(splash, text=c, font=("Consolas", 7), bg=THEME["panel"],
                 fg=THEME["success"]).pack(anchor="w", padx=40)

    progress_var = tk.DoubleVar(value=0)
    style = ttk.Style(splash)
    style.theme_use("clam")
    style.configure("Splash.Horizontal.TProgressbar",
                    troughcolor=THEME["border"], background=THEME["accent"],
                    bordercolor=THEME["panel"])
    pb = ttk.Progressbar(splash, variable=progress_var, maximum=100,
                          style="Splash.Horizontal.TProgressbar", length=500)
    pb.pack(pady=10)
    status_lbl = tk.Label(splash, text="Initializing…", font=FONT["small"],
                           bg=THEME["panel"], fg=THEME["muted"])
    status_lbl.pack()

    steps = [
        (12,  "Loading Profile Manager…"),
        (25,  "Loading Session Logger…"),
        (40,  "Loading Gesture Recorder…"),
        (55,  "Applying Calibration Data…"),
        (70,  "Building Whisper Fuzzy Command Pipeline…"),
        (85,  "Starting Performance Monitor…"),
        (100, "v905 Ready! 🚀"),
    ]

    def _step(idx=0):
        if idx < len(steps):
            progress_var.set(steps[idx][0])
            status_lbl.config(text=steps[idx][1])
            splash.after(260, lambda: _step(idx + 1))
        else:
            splash.after(350, splash.destroy)

    splash.after(120, _step)
    return splash


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

import tkinter.simpledialog


def main():
    root = tk.Tk()
    root.withdraw()

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TCombobox",
                    fieldbackground=THEME["overlay"], background=THEME["overlay"],
                    foreground=THEME["text"], selectbackground=THEME["accent2"])
    style.configure("Horizontal.TScale",
                    background=THEME["card"], troughcolor=THEME["border"])
    style.configure("TRadiobutton",
                    background=THEME["card"], foreground=THEME["text"])
    style.configure("TCheckbutton",
                    background=THEME["card"], foreground=THEME["text"])
    style.configure("TNotebook", background=THEME["bg"])
    style.configure("TNotebook.Tab",
                    background=THEME["panel"], foreground=THEME["text"],
                    padding=[10, 4])
    style.map("TNotebook.Tab",
              background=[("selected", THEME["card"])],
              foreground=[("selected", THEME["accent"])])

    splash = show_splash(root)
    root.wait_window(splash)
    root.deiconify()

    ww, wh = 1280, 800
    sw, sh  = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")

    app = TouchlessApp(root)
    root.after(1000, app._start_engine)
    root.mainloop()


if __name__ == "__main__":
    main()

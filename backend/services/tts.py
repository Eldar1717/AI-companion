"""Piper TTS engine with per-phoneme timing for lip-sync.

Pipeline: text -> espeak-ng phonemes -> VITS ONNX -> PCM audio.
The voice model is patched in memory (``include_alignments=True``) so that the
model exposes the per-phoneme sample counts, which we turn into absolute
timestamps used by the browser for lip movement.

Important (and deliberately honest): the browser receives *phoneme classes* with
timings - it cannot do frame accurate mouth tracking on a static image. We map
each phoneme to an approximate "mouth openness" so that closed sounds (m, b, n)
do not move the lips and vowels open them. That is a stylised lip-sync, not
photorealistic motion capture.
"""

from __future__ import annotations

import base64
import io
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend import config

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy comes with piper-tts
    np = None  # type: ignore

log = logging.getLogger(__name__)

# Vowels (espeak IPA used by Piper Russian voices) -> open mouth
_VOWELS = set(
    "aeiouyɑɔʌɪʊɨəɛɔæɒㅟʏɤøɶɑ̃ɔ̃ɛ̃ĩũ"
    "aɑeɛioɔuʊyɨəɛæə̃"
)
# Characters that are never spoken / handled specially
_SILENCE = {" ", "_", "^", "$", "?", "!", "."}
_STRESS = "ˈ"


def phoneme_openness(phoneme: str) -> float:
    """Approximate mouth openness for a phoneme symbol (0 closed - 1 open)."""
    if not phoneme:
        return 0.15
    if phoneme in _SILENCE:
        return 0.12
    if phoneme == _STRESS:
        return 0.8
    # strip combining marks / stress
    base = phoneme.replace("ˈ", "")
    if base in _VOWELS:
        return 1.0
    if base in ("j", "r", "l", "ɫ", "ɹ", "ɻ", "ʋ", "β", "ɸ"):
        return 0.5
    return 0.25


_CYRILLIC = "абвгдежзийклмнопрстуфхцчшщъыьэюяё"
_KEEP_ASCII = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " .,!?;:%()[]\"'-—–«»…·+=/@#$&*~"
)


def sanitize_for_speech(text: str) -> str:
    """Remove markup that espeak would try to pronounce."""
    if not text:
        return ""
    out = _markdown_strip(text)
    kept = _CYRILLIC + _CYRILLIC.upper()
    out = "".join(ch for ch in out if ch in _KEEP_ASCII or ch in kept)
    out = out.replace("\n", " ").replace("\t", " ")
    out = " ".join(out.split())
    return out.strip()


def _markdown_strip(text: str) -> str:
    import re

    # [text](url) -> text, ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # emphasis / code / strikethrough markers
    text = re.sub(r"[*_`~]+", "", text)
    # blockquote markers, headings
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return text


class TtsEngine:
    """Lazy, thread-safe Piper wrapper."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._voice: Any = None
        self._voice_name: str = ""
        self._voice_path: str = ""
        self._import_error: str = ""
        self._started = False
        self._last_retry_at = 0.0

    # ------------------------------------------------------------------ discovery
    @staticmethod
    def discover_voices() -> List[Dict[str, str]]:
        voices: List[Dict[str, str]] = []
        seen = set()
        for directory in config.PIPER_MODEL_DIRS:
            root = Path(directory).expanduser()
            if not root.exists():
                continue
            for model in sorted(root.glob("*.onnx")):
                if model.name.endswith(".aligned.onnx"):
                    continue
                name = model.stem
                if name in seen:
                    continue
                seen.add(name)
                voices.append(
                    {
                        "name": name,
                        "path": str(model),
                        "dir": str(root),
                        "has_config": (model.with_suffix(".json")).exists(),
                    }
                )
        return voices

    def ensure_started(self) -> None:
        with self._lock:
            if self._voice is not None:
                return
            # Not loaded yet (initial attempt failed, or voices were
            # downloaded after startup): re-run discovery. The check is cheap
            # (a few directory globs) so polling status() is safe.
            if not self._started:
                self._started = True
            try:
                import piper  # noqa: F401
            except Exception as exc:  # ImportError or binary issues
                self._import_error = f"piper-tts не установлен: {exc}"
                log.error(self._import_error)
                return
            voices = self.discover_voices()
            if not voices:
                self._import_error = (
                    "голоса Piper не найдены в " + ", ".join(config.PIPER_MODEL_DIRS)
                )
                log.error(self._import_error)
                return
            from backend.settings import store

            wanted = store().get("voice.voice", "") or voices[0]["name"]
            chosen = next((v for v in voices if v["name"] == wanted), voices[0])
            self.load(chosen["name"])

    def load(self, name: str) -> bool:
        from backend.settings import store

        with self._lock:
            voices = self.discover_voices()
            chosen = next((v for v in voices if v["name"] == name), None)
            if chosen is None:
                log.warning("Голос %r не найден; доступны: %s", name, [v["name"] for v in voices])
                return False
            try:
                from piper import PiperVoice

                voice = PiperVoice.load(
                    chosen["path"],
                    espeak_data_dir=_espeak_dir(),
                    include_alignments=True,
                )
            except Exception as exc:
                self._import_error = f"не удалось загрузить голос {name}: {exc}"
                log.exception(self._import_error)
                return False
            self._voice = voice
            self._voice_name = name
            self._voice_path = chosen["path"]
            self._import_error = ""
            store().set_many({"voice.voice": name})
            log.info("Голос Piper загружен: %s (%s)", name, chosen["path"])
            return True

    def status(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = {
                "ready": self._voice is not None,
                "voice": self._voice_name,
                "path": self._voice_path,
                "voices": [v["name"] for v in self.discover_voices()],
                "error": self._import_error,
                "streaming": self._voice is not None,
            }
        # Voices appeared after the first startup attempt? Retry loading in
        # the background so the UI recovers without a restart.
        if not snapshot["ready"] and snapshot["voices"]:
            now = time.time()
            if now - self._last_retry_at > 5:
                self._last_retry_at = now
                threading.Thread(target=self.ensure_started, name="ai-companion-tts-retry", daemon=True).start()
        return snapshot

    # ------------------------------------------------------------------ synthesis
    def synthesize_frames(
        self, text: str, rate: float = 1.0, volume: float = 1.0
    ) -> Optional[Tuple[List[Dict[str, Any]], int, float]]:
        """Return JSON-ready frames plus sample rate and total duration."""
        self.ensure_started()
        with self._lock:
            voice = self._voice
            if voice is None:
                return None

            from piper import SynthesisConfig

            clean = sanitize_for_speech(text)
            if not clean:
                # Text sanitized to empty (emoji-only, markdown-only, etc.).
                # Return actual silence so the frontend receives a playable frame.
                if np is not None:
                    silence = base64.b64encode(
                        (np.zeros(int(0.2 * 22050), dtype="<i2")).tobytes()
                    ).decode("ascii")
                else:
                    silence = base64.b64encode(
                        bytes(int(0.2 * 22050) * 2)
                    ).decode("ascii")
                return (
                    [
                        {
                            "type": "frame",
                            "t": 0.0,
                            "dur": 0.2,
                            "p": "",
                            "open": 0.12,
                            "text": "",
                            "audio": silence,
                        }
                    ],
                    22050,
                    0.2,
                )

            length_scale = 1.0 / max(0.5, min(3.0, rate))
            frames: List[Dict[str, Any]] = []
            t = 0.0
            chunk_count = 0
            total_samples = 0
            for chunk in voice.synthesize(
                clean,
                syn_config=SynthesisConfig(length_scale=length_scale, volume=volume),
                include_alignments=True,
            ):
                chunk_count += 1
                audio = chunk.audio_float_array
                total_samples += len(audio)
                sample_rate = chunk.sample_rate
                boundaries = self._phoneme_boundaries(chunk, len(audio))
                if not boundaries and len(audio) > 0:
                    # Fallback: no phoneme alignments available — use whole chunk.
                    boundaries = [(0, len(audio), "")]
                for (start, end, phoneme) in boundaries:
                    segment = audio[start:end]
                    dur = len(segment) / sample_rate
                    frames.append(
                        {
                            "type": "frame",
                            "t": round(t, 4),
                            "dur": round(dur, 4),
                            "p": phoneme,
                            "open": phoneme_openness(phoneme),
                            "text": "",
                            "audio": base64.b64encode(
                                (segment * 32767).astype("<i2").tobytes()
                            ).decode("ascii"),
                        }
                    )
                    t += dur
            if not frames:
                log.warning(
                    "TTS: synthesis returned no audio chunks. "
                    "text=%r chunks=%d samples=%d sample_rate=%d espeak_dir=%s",
                    clean[:60], chunk_count, total_samples, sample_rate, _espeak_dir(),
                )
            return frames, sample_rate, t

    def synthesize_wav(
        self, text: str, rate: float = 1.0, volume: float = 1.0
    ) -> Optional[Tuple[bytes, int, float, int]]:
        """Return WAV bytes, sample rate, duration and number of phoneme frames."""
        result = self.synthesize_frames(text, rate, volume)
        if result is None:
            return None
        frames, sample_rate, duration = result
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            total = 0
            for frame in frames:
                raw = base64.b64decode(frame["audio"])
                handle.writeframes(raw)
                total += len(raw) // 2
        return buf.getvalue(), sample_rate, duration, total

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _phoneme_boundaries(chunk: Any, audio_len: int) -> List[Tuple[int, int, str]]:
        """Convert phoneme alignments into (start_sample, end_sample, symbol)."""
        aligns = chunk.phoneme_alignments
        ids = chunk.phoneme_ids
        phonemes = list(chunk.phonemes or [])
        boundaries: List[Tuple[int, int, str]] = []

        if aligns:
            pos = 0
            for align in aligns:
                samples = int(align.num_samples)
                start = min(pos, max(0, audio_len - 1))
                end = min(pos + samples, audio_len)
                if end > start:
                    symbol = align.phoneme if align.phoneme else ""
                    boundaries.append((start, end, symbol))
                pos = end
            if pos < audio_len:
                boundaries.append((pos, audio_len, ""))
            return boundaries

        if ids and audio_len:
            # No alignments available: split the chunk evenly between phoneme ids.
            step = max(1, audio_len // len(ids))
            symbols = phonemes if len(phonemes) == len(ids) else [""] * len(ids)
            pos = 0
            for idx in range(len(ids)):
                start = pos
                end = min(pos + step, audio_len)
                if end > start:
                    boundaries.append((start, end, symbols[idx] if idx < len(symbols) else ""))
                pos = end
            if pos < audio_len:
                boundaries.append((pos, audio_len, ""))
        return boundaries


def _espeak_dir() -> Path:
    try:
        import piper

        found = Path(piper.__file__).resolve().parent / "espeak-ng-data"
        return found
    except Exception:  # pragma: no cover
        return Path(".")


_engine: Optional[TtsEngine] = None
_lock = threading.RLock()


def engine() -> TtsEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = TtsEngine()
        return _engine


def timed(fn):  # pragma: no cover - debug helper
    start = time.time()
    value = fn()
    log.debug("tts %s took %.3fs", getattr(fn, "__name__", "call"), time.time() - start)
    return value

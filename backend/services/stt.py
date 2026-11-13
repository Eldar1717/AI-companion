"""Local Whisper speech-to-text.

Audio arrives from the browser as a WebM/Opus (or WAV) blob produced by
MediaRecorder. We decode it with PyAV, downmix to 16 kHz mono, run a cheap energy
gate (so silence never reaches Whisper) and transcribe with faster-whisper.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import wave
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend import config

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# RMS below this is treated as "no speech" (browser noise floor / silence).
ENERGY_GATE = 0.004
MIN_AUDIO_SECONDS = 0.25

_CONTENT_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg;codecs=opus": "ogg",
}


class AudioDecodeError(Exception):
    """Raised when the browser blob cannot be decoded."""


def decode_audio(raw: bytes, content_type: str = "") -> np.ndarray:
    """Return mono float32 samples at 16 kHz in [-1, 1]."""
    if not raw:
        raise AudioDecodeError("пустой аудиофайл")
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in ("audio/wav", "audio/x-wav", "audio/wave") or raw[:4] == b"RIFF":
        return _decode_wav(raw)
    try:
        return _decode_av(raw)
    except AudioDecodeError:
        raise
    except Exception as exc:
        raise AudioDecodeError(f"не удалось разобрать аудио ({kind or 'unknown'}): {exc}") from exc


def _decode_wav(raw: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(raw), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise AudioDecodeError("ожидается 16-bit PCM WAV")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        data = _resample_linear(data, rate, SAMPLE_RATE)
    return data


def _resample_linear(data: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or data.size == 0:
        return data
    ratio = dst / float(src)
    out_len = max(1, int(round(data.size * ratio)))
    idx = np.arange(out_len) / ratio
    idx0 = np.floor(idx).astype(int)
    idx1 = np.minimum(idx0 + 1, data.size - 1)
    frac = idx - idx0
    return (data[idx0] * (1 - frac) + data[idx1] * frac).astype(np.float32)


def _decode_av(raw: bytes) -> np.ndarray:
    import av

    # Auto-detect the container format (webm, ogg, mp4, ...).
    # Passing format="" disables detection and raises "no container format".
    container = av.open(io.BytesIO(raw))
    try:
        stream = None
        for candidate in container.streams:
            if candidate.type == "audio":
                stream = candidate
                break
        if stream is None:
            raise AudioDecodeError("в файле нет аудиопотока")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
        chunks: List[np.ndarray] = []
        for frame in container.decode(stream):
            resampled = resampler.resample(frame)
            if resampled is None:
                continue
            if isinstance(resampled, list):
                for item in resampled:
                    chunks.append(_frame_to_array(item))
            else:
                chunks.append(_frame_to_array(resampled))
        if not chunks:
            raise AudioDecodeError("в файле нет образцов звука")
        return np.concatenate(chunks).astype(np.float32)
    finally:
        container.close()


def _frame_to_array(frame: Any) -> np.ndarray:
    # PyAV >= 19: frame.to_ndarray() gives (channels, samples) for planar
    # formats like fltp; flatten to a mono 1-D array.
    try:
        arr = frame.to_ndarray()
    except AttributeError:
        # older PyAV: fall back to the plane API
        planes = frame.planes
        if not planes:
            return np.zeros(0, dtype=np.float32)
        arr = planes[0].to_ndarray()
    return np.asarray(arr, dtype=np.float32).reshape(-1)


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(math.sqrt(float(np.mean(audio.astype(np.float64) ** 2))))


class SttEngine:
    """Lazy wrapper around faster-whisper with a single worker lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model: Any = None
        self._error: str = ""
        self._started = False
        self._last_check = 0.0

    def ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            try:
                import faster_whisper  # noqa: F401
            except Exception as exc:
                self._error = f"faster-whisper не установлен: {exc}"
                log.error(self._error)
                return
            try:
                from faster_whisper import WhisperModel

                size = config.STT_MODEL
                log.info("Загрузка Whisper model %s (first run downloads it)", size)
                self._model = WhisperModel(
                    size,
                    device=config.STT_DEVICE,
                    compute_type=config.STT_COMPUTE_TYPE,
                    download_root=str(config.WHISPER_CACHE_DIR),
                )
                self._error = ""
                log.info("Whisper model %s готов", size)
            except Exception as exc:
                self._model = None
                self._error = f"не удалось загрузить Whisper ({size}): {exc}"
                log.exception(self._error)

    def status(self) -> Dict[str, Any]:
        # deliberately does NOT trigger a (potentially slow, network-bound) load:
        # startup warms the engine up in a background thread.
        with self._lock:
            return {
                "ready": self._model is not None,
                "initialising": self._started and self._model is None,
                "model": config.STT_MODEL,
                "error": self._error,
                "device": config.STT_DEVICE,
                "compute_type": config.STT_COMPUTE_TYPE,
            }

    def transcribe(
        self, audio: np.ndarray, language: str = "ru"
    ) -> Dict[str, Any]:
        self.ensure_started()
        with self._lock:
            model = self._model
        if model is None:
            return {"ok": False, "error": "STT_UNAVAILABLE", "message": self._error or "Whisper не готов"}

        duration = len(audio) / float(SAMPLE_RATE)
        if duration < MIN_AUDIO_SECONDS:
            return {"ok": True, "text": "", "empty": True, "duration": duration}
        if rms(audio) < ENERGY_GATE:
            return {"ok": True, "text": "", "empty": True, "duration": duration}

        try:
            segments, info = model.transcribe(
                np.ascontiguousarray(audio),
                language=language,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                temperature=0.0,
                no_speech_threshold=0.65,
                compression_ratio_threshold=2.4,
            )
            pieces: List[Dict[str, Any]] = []
            parts: List[str] = []
            for segment in segments:
                text = (segment.text or "").strip()
                if not text:
                    continue
                pieces.append(
                    {
                        "start": round(segment.start, 3),
                        "end": round(segment.end, 3),
                        "text": text,
                    }
                )
                parts.append(text)
            return {
                "ok": True,
                "text": " ".join(parts).strip(),
                "empty": not parts,
                "duration": duration,
                "language": info.language,
                "segments": pieces,
            }
        except Exception as exc:
            log.exception("Ошибка распознавания")
            return {"ok": False, "error": "STT_ERROR", "message": str(exc)}


_engine: Optional[SttEngine] = None
_lock = threading.RLock()


def engine() -> SttEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = SttEngine()
        return _engine

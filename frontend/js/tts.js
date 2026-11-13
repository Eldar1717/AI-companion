/**
 * TTS playback — HTML5 <audio> with WAV from /api/tts (primary).
 *
 * Safari блокирует audio.play() без user gesture. Решение: unlock Audio
 * element при первом клике пользователя (играть тихую WAV), потом Piper
 * WAV играет без ограничений.
 *
 * Fallback: browser speechSynthesis если /api/tts не дошёл.
 */

import { api } from './api.js';

// Shared unlocked Audio element — created on first user gesture
let _unlockedAudio = null;

/** Generate a tiny silent WAV blob for unlocking. */
function createSilentWav(durationSec = 0.05, sampleRate = 22050) {
  const numSamples = Math.floor(durationSec * sampleRate);
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);
  view.setUint32(0, 0x52494646);          // "RIFF"
  view.setUint32(4, 36 + numSamples * 2); // file size
  view.setUint32(8, 0x57415645);          // "WAVE"
  view.setUint32(12, 0x666d7420);         // "fmt "
  view.setUint32(16, 16);                 // chunk size
  view.setUint16(20, 1);                  // PCM
  view.setUint16(22, 1);                  // mono
  view.setUint32(24, sampleRate);
  view.setUint32(28, sampleRate * 2);
  view.setUint16(32, 2);
  view.setUint16(34, 16);
  view.setUint32(36, 0x64617461);         // "data"
  view.setUint32(40, numSamples * 2);     // data size
  return new Blob([buffer], { type: 'audio/wav' });
}

/**
 * Unlock Audio element by playing silent WAV during user gesture.
 * Called once on first click/keydown — subsequent audio.play() calls work.
 */
export function unlockAudio() {
  if (_unlockedAudio) return _unlockedAudio;
  const audio = new Audio();
  const blob = createSilentWav(0.05);
  const url = URL.createObjectURL(blob);
  audio.src = url;
  audio.volume = 0;
  audio.play().then(() => {
    // Clean up silent audio shortly after unlock
    setTimeout(() => {
      try { audio.pause(); audio.currentTime = 0; } catch (e) {}
      try { audio.src = ''; } catch (e) {}
      try { URL.revokeObjectURL(url); } catch (e) {}
    }, 300);
  }).catch(() => {
    // Unlock failed — will try speechSynthesis fallback
    console.warn('Audio unlock failed');
  });
  _unlockedAudio = audio;
  return audio;
}

export class TtsPlayer {
  constructor(options = {}) {
    this.onFrame = options.onFrame || (() => {});
    this.onEnd = options.onEnd || (() => {});
    this.onError = options.onError || (() => {});
    this.onStart = options.onStart || (() => {});
    this.audio = null;
    this.frames = [];
    this.playing = false;
    this.startTime = 0;
    this.volume = 1;
    this._ended = false;
    this._currentOnEnd = null;
    this._currentOnFrames = null;
  }

  setVolume(value) {
    this.volume = Math.max(0, Math.min(2, value));
    if (this.audio) this.audio.volume = Math.min(1, this.volume);
  }

  get elapsed() {
    return this.playing ? (performance.now() - this.startTime) / 1000 : 0;
  }

  getLevel() {
    return this.playing ? 0.5 : 0;
  }

  _callOnEnd(reason) {
    if (this._ended) return;
    this._ended = true;
    this.playing = false;
    this.frames = [];
    if (this._currentOnEnd) this._currentOnEnd(reason);
    else if (this.onEnd) this.onEnd(reason);
  }

  _callOnStart(frames) {
    this._ended = false;
    this.playing = true;
    this.startTime = performance.now();
    if (this._currentOnFrames) this._currentOnFrames(frames);
    if (this.onStart) this.onStart(frames);
  }

  stop(reason = 'stopped') {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    if (this.audio) {
      try { this.audio.pause(); } catch (err) {}
      try { this.audio.currentTime = 0; } catch (err) {}
    }
    if (typeof window.speechSynthesis !== 'undefined') {
      try { window.speechSynthesis.cancel(); } catch (err) {}
    }
    this._callOnEnd(reason);
    // Resolve any pending speak() promise so the queue doesn't hang
    if (this._resolveFinished) {
      const r = this._resolveFinished;
      this._resolveFinished = null;
      r();
    }
  }

  /**
   * Speak `text` using /api/tts WAV + <audio> element (primary).
   * Falls back to browser speechSynthesis if /api/tts fails.
   */
  async speak(text, options = {}) {
    const rate = options.rate ?? 1;
    const volume = options.volume ?? this.volume;
    this.setVolume(volume);
    this._currentOnEnd = options.onEnd || null;
    this._currentOnFrames = options.onFrames || null;
    this._ended = false;
    this.stop('interrupted');
    this._ended = false;
    this._timer = null;

    // Promise that resolves when audio finishes — _queueSpeech awaits this
    let resolveFinished;
    const finishedPromise = new Promise((resolve) => { resolveFinished = resolve; });
    this._resolveFinished = resolveFinished;

    // ── Primary: /api/tts WAV + <audio> element ──────────────────────
    try {
      const response = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, rate, volume }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error((data && data.message) || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const durationSec = parseFloat(response.headers.get('X-Duration') || '0');
      const estFrames = Math.max(1, Math.round(durationSec / 0.05));

      return this._playWav(url, estFrames, text, durationSec, { rate, volume, options }, finishedPromise);
    } catch (err) {
      console.warn('TTS WAV failed, falling back to speechSynthesis:', err);
      if (typeof window.speechSynthesis !== 'undefined' && typeof SpeechSynthesisUtterance !== 'undefined') {
        return this._speakBrowser(text, { rate, volume, options }, finishedPromise);
      }
      this.onError(err);
      resolveFinished();
      throw err;
    }
  }

  _playWav(url, estFrames, text, durationSec, params, finishedPromise) {
    const { rate, volume, options } = params;
    // Use unlocked audio element if available, otherwise create new
    if (_unlockedAudio) {
      this.audio = _unlockedAudio;
    } else if (!this.audio) {
      this.audio = new Audio();
    }
    // Always set handlers — they may not be set on unlocked element
    this.audio.onended = () => {
      this._callOnEnd('finished');
      if (this._resolveFinished) this._resolveFinished();
    };
    this.audio.onerror = () => {
      this._callOnEnd('error');
      if (this._resolveFinished) this._resolveFinished();
    };

    this.audio.src = url;
    this.audio.volume = Math.min(1, volume);
    this.audio.playbackRate = Math.max(0.5, Math.min(3.0, rate));

    // Timer fallback — use real duration from X-Duration header, generous buffer
    const realDurationMs = durationSec ? durationSec * 1000 : estFrames * 50;
    const timer = setTimeout(() => {
      if (!this._ended) {
        this._callOnEnd('finished');
        if (this._resolveFinished) this._resolveFinished();
      }
    }, realDurationMs + 2000);
    this._timer = timer;

    this.audio.play().then(() => {
      // Build estimated frames for avatar lip-sync
      const frames = [];
      const frameDur = 0.05;
      for (let i = 0; i < estFrames; i++) {
        frames.push({ t: i * frameDur, dur: frameDur, p: '', open: 0.5, text: '', audio: '' });
      }
      this.frames = frames;
      this._callOnStart(frames);
    }).catch((err) => {
      console.warn('audio.play() failed, falling back to speechSynthesis:', err);
      this._cleanupWav(url);
      if (typeof window.speechSynthesis !== 'undefined') {
        return this._speakBrowser(text, { rate, volume, options }, finishedPromise);
      }
      this.onError(err);
      if (this._resolveFinished) this._resolveFinished();
    });

    return finishedPromise;
  }

  _cleanupWav(url) {
    try { URL.revokeObjectURL(url); } catch (err) {}
    if (this.audio) {
      try { this.audio.pause(); } catch (err) {}
      try { this.audio.src = ''; } catch (err) {}
    }
  }

  // ────────────────────────────────────────────────────────────────────
  // Browser speechSynthesis (fallback path)
  // ────────────────────────────────────────────────────────────────────

  _speakBrowser(text, params = {}, finishedPromise) {
    const { rate, volume, options } = params;
    if (typeof window.speechSynthesis === 'undefined') {
      this.onError(new Error('speechSynthesis недоступен'));
      if (finishedPromise) finishedPromise();
      return;
    }
    try { window.speechSynthesis.cancel(); } catch (err) {}

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'ru-RU';
    utterance.rate = Math.max(0.5, Math.min(3.0, rate));
    utterance.volume = Math.max(0, Math.min(1, volume));
    utterance.pitch = 1.04;

    const voices = window.speechSynthesis.getVoices();
    const ruVoice = voices.find(v => v.lang && v.lang.toLowerCase().startsWith('ru'));
    if (ruVoice) utterance.voice = ruVoice;

    const estDuration = Math.max(0.5, text.length / 10);

    const resolveAndEnd = (reason) => {
      this._callOnEnd(reason);
      if (finishedPromise) finishedPromise();
    };

    utterance.onstart = () => {
      this._callOnStart([]);
      this.onFrame([]);
    };
    utterance.onend = () => resolveAndEnd('finished');
    utterance.onerror = (event) => {
      console.warn('speechSynthesis error:', event);
      resolveAndEnd('error');
    };

    const timer = setTimeout(() => {
      if (!this._ended) resolveAndEnd('finished');
    }, estDuration * 1000 + 2000);
    this._timer = timer;

    window.speechSynthesis.speak(utterance);
    return finishedPromise;
  }

  ensureContext() {
    return Promise.resolve(true);
  }
}

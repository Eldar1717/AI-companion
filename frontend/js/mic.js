/**
 * Microphone capture with voice activity detection (VAD), phrase segmentation
 * and barge-in detection.
 *
 * Signal flow:
 *   mic -> AudioContext/Analyser (level only, never sent) -> MediaRecorder
 *   -> on phrase end -> blob -> /api/stt
 *
 * Echo avoidance: while the avatar speaks we compare the microphone level with
 * the playback level reported by the TTS player. A barge-in is only accepted
 * when the microphone is clearly louder than the playback, which prevents the
 * avatar from hearing herself through the speaker. This is a level-based gate,
 * not acoustic echo cancellation - see README.
 */

const DEFAULTS = {
  vadThreshold: 0.02,
  silenceMs: 700,
  minSpeechMs: 220,
  bargeInDb: -16,
};

export class Microphone {
  constructor(options = {}) {
    this.options = Object.assign({}, DEFAULTS, options);
    this.onLevel = options.onLevel || (() => {});
    this.onPhraseStart = options.onPhraseStart || (() => {});
    this.onPhraseEnd = options.onPhraseEnd || (() => {});
    this.onBargeIn = options.onBargeIn || (() => {});
    this.onError = options.onError || (() => {});
    this.getPlaybackLevel = options.getPlaybackLevel || (() => 0);

    this.stream = null;
    this.recorder = null;
    this.analyser = null;
    this.audioContext = null;
    this.chunks = [];
    this.running = false;
    this.speaking = false;
    this.silenceTimer = 0;
    this.phraseStart = 0;
    this.timer = null;
    this.level = 0;
  }

  get active() {
    return this.running;
  }

  async _ensureContext() {
    if (!this.audioContext) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) throw new Error('AudioContext недоступен');
      this.audioContext = new Ctx();
    }
    if (this.audioContext.state === 'suspended') {
      try {
        await this.audioContext.resume();
      } catch (err) {
        /* ignore */
      }
    }
    return this.audioContext;
  }

  static pickMimeType() {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
    ];
    for (const type of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    return '';
  }

  async start() {
    if (this.running) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.onError(new Error('Браузер не поддерживает запись микрофона'));
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
    } catch (err) {
      this.onError(err);
      return;
    }

    const context = await this._ensureContext();
    const source = context.createMediaStreamSource(this.stream);
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.smoothingTimeConstant = 0.6;
    source.connect(this.analyser);

    const mimeType = Microphone.pickMimeType();
    try {
      this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    } catch (err) {
      this.recorder = new MediaRecorder(this.stream);
    }
    this.chunks = [];
    this.recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    };
    this.recorder.onstop = () => this._finishPhrase();
    this.recorder.onerror = (event) => this.onError(event.error || new Error('Ошибка записи'));
    this.recorder.start(250);

    this.running = true;
    this.speaking = false;
    this.silenceTimer = 0;
    this.phraseStart = 0;
    this.timer = setInterval(() => this._tick(), 50);
  }

  stop() {
    if (!this.running) return;
    this.running = false;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.stop();
    } else {
      this._finishPhrase();
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
  }

  _tick() {
    if (!this.analyser) return;
    const buffer = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(buffer);
    const rms = computeRms(buffer);
    this.level = rms;
    this.onLevel(rms);

    const threshold = this.speaking ? this.options.vadThreshold : this.options.vadThreshold;
    const dBfs = 20 * Math.log10(Math.max(1e-6, rms));
    const playbackLevel = this.getPlaybackLevel();
    const micRisesAbovePlayback = rms > playbackLevel * 1.15;
    const bargeIn = dBfs > this.options.bargeInDb && micRisesAbovePlayback;

    if (!this.speaking) {
      // Waiting for the first syllable.
      if (rms > threshold || bargeIn) {
        this.speaking = true;
        this.phraseStart = performance.now();
        this.silenceTimer = 0;
        this.onPhraseStart(rms, dBfs);
      }
      return;
    }

    // Inside a phrase: a barge-in (only possible while the avatar speaks) wins.
    if (bargeIn && playbackLevel > 0) {
      this.onBargeIn(rms, dBfs);
      return;
    }
    if (rms > threshold) {
      this.silenceTimer = 0;
      return;
    }
    this.silenceTimer += 50;
    if (this.silenceTimer >= this.options.silenceMs) {
      const duration = performance.now() - this.phraseStart;
      if (duration < this.options.minSpeechMs) {
        // Too short - treat as noise and keep listening.
        this.speaking = false;
        this.silenceTimer = 0;
        return;
      }
      this._stopRecording();
    }
  }

  _stopRecording() {
    this.speaking = false;
    this.silenceTimer = 0;
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.stop();
    } else {
      this._finishPhrase();
    }
  }

  _finishPhrase() {
    if (!this.chunks.length) return;
    const blob = new Blob(this.chunks, { type: this.recorder && this.recorder.mimeType });
    this.chunks = [];
    if (blob.size < 2000) return;
    this.onPhraseEnd(blob);
  }

  /** Force-stop the current phrase (used by the «Завершить» button). */
  forceStop() {
    if (this.speaking) this._stopRecording();
    else this.stop();
  }
}

function computeRms(buffer) {
  let sum = 0;
  for (let i = 0; i < buffer.length; i++) {
    const value = (buffer[i] - 128) / 128;
    sum += value * value;
  }
  return Math.sqrt(sum / buffer.length);
}

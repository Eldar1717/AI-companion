/**
 * Conversation state machine:
 *
 *   text / voice -> LLM (streaming) -> sentences -> Piper TTS -> avatar
 *
 * While the model generates, complete sentences are pushed to the TTS queue so
 * the avatar starts talking before the whole reply is ready.
 */

import { api } from './api.js';

const SENTENCE_END = /[.!?…]+["')\]]*\s+|[.!?…]+$|\n+/;

export class Conversation {
  constructor(options = {}) {
    this.avatar = options.avatar;
    this.player = options.player;
    this.mic = options.mic;
    this.settings = options.settings || {};
    this.soundEnabled = options.soundEnabled !== false;
    this.streamSpeech = options.streamSpeech !== false;

    this.onStatus = options.onStatus || (() => {});
    this.onUserMessage = options.onUserMessage || (() => {});
    this.onAssistantStart = options.onAssistantStart || (() => {});
    this.onAssistantDelta = options.onAssistantDelta || (() => {});
    this.onAssistantFinal = options.onAssistantFinal || (() => {});
    this.onError = options.onError || (() => {});
    this.onToast = options.onToast || (() => {});

    this.listening = false;
    this.busy = false;
    this.text = '';
    this.spoken = 0;
    this.buffer = '';
    this.queue = Promise.resolve();
    this.abortController = null;
    this.currentController = null;
    this.replyText = '';
    this.meta = null;
  }

  get busy_() {
    return this.busy || this.listening;
  }

  /* ------------------------------------------------------------------- voice */
  startVoice() {
    if (this.listening) return;
    if (!this.mic) return;
    this.listening = true;
    this.mic.start();
    this.onStatus('listening');
  }

  stopVoice() {
    if (!this.listening) return;
    this.listening = false;
    if (this.mic) this.mic.stop();
    this.onStatus(this.busy ? 'thinking' : 'idle');
  }

  endConversation() {
    this.stopVoice();
    this.interrupt('conversation ended');
    this.onStatus('idle');
    this.onToast('Голосовой диалог завершён', 'ok');
  }

  interrupt(reason = 'interrupted') {
    if (this.player && this.player.playing) {
      this.player.stop(reason);
      this.avatar && this.avatar.clearSpeech();
    }
    this.queue = Promise.resolve();
    this.buffer = '';
    this.spoken = 0;
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
  }

  /* --------------------------------------------------------------------- text */
  async sendText(message, source = 'text') {
    if (!message || !message.trim()) return;
    this.interrupt('new message');
    this.busy = true;
    this.buffer = '';
    this.spoken = 0;
    this.onStatus('thinking');
    this.onUserMessage(message.trim(), source);

    const controller = new AbortController();
    this.abortController = controller;
    this.currentController = controller;

    try {
      await api.stream(
        '/api/chat',
        { message: message.trim(), source, stream: true },
        {
          signal: controller.signal,
          onEvent: (event) => this._handleEvent(event, controller),
        }
      );
    } catch (err) {
      if (err.name === 'AbortError') {
        this.onToast('Ответ прерван', 'ok');
      } else {
        this._fail(err);
        return;
      }
    } finally {
      this.busy = false;
      this.abortController = null;
    }

    const reply = (this.replyText || this.buffer || '').trim();
    if (reply) {
      const rest = reply.slice(this.spoken).trim();
      if (rest) {
        this.spoken = reply.length;
        await this._speak(rest);
      }
      this.onAssistantFinal(reply);
    }
    if (!this.listening) {
      this.onStatus(this.player && this.player.playing ? 'speaking' : 'idle');
    }
  }

  _handleEvent(event, controller) {
    if (event.type === 'meta') {
      this.meta = event;
      return;
    }
    if (event.type === 'status') {
      return;
    }
    if (event.type === 'token') {
      this.buffer += event.text;
      this.onAssistantDelta(this.buffer);
      if (this.streamSpeech && this.soundEnabled) this._flushSentences();
      return;
    }
    if (event.type === 'done') {
      this.replyText = event.text || this.buffer;
      return;
    }
    if (event.type === 'error') {
      const err = new Error(event.message || event.error);
      err.code = event.error;
      err.status = event.status;
      controller.abort();
      throw err;
    }
  }

  _flushSentences() {
    if (!this.buffer) return;
    const rest = this.buffer.slice(this.spoken);
    const match = SENTENCE_END.exec(rest);
    if (!match) return;
    const end = match.index + match[0].length;
    const sentence = this.buffer.slice(this.spoken, this.spoken + end).trim();
    this.spoken += end;
    if (sentence.length >= 2) {
      this._queueSpeech(sentence);
    }
  }

  _queueSpeech(text) {
    this.queue = this.queue
      .then(() => this._speak(text))
      .catch((err) => this.onError(err));
  }

  async _speak(text) {
    if (!this.soundEnabled || !this.player) return;
    // AudioContext must be created inside a user gesture chain.
    await this.player.ensureContext().catch((err) => this.onError(err));
    this.onStatus('speaking');
    try {
      await this.player.speak(text, {
        rate: this.settings.voice?.rate ?? 1,
        volume: this.settings.voice?.volume ?? 1,
        onFrames: (frames) => {
          this.avatar.pushSpeech(frames);
          if (this.avatar) this.avatar.setEmotion('speaking');
        },
        onEnd: () => {
          this.avatar && this.avatar.clearSpeech();
          // Safari blocks getUserMedia without user gesture — cannot
          // auto-restart mic. Set status to idle; mic restarts on
          // the next voice button press (which provides the gesture).
          this.onStatus('idle');
        },
      });
    } catch (err) {
      this.onError(err);
    }
  }

  _fail(err) {
    this.busy = false;
    const code = err.code || 'ERROR';
    let message = err.message || 'ошибка';
    if (code === 'OLLAMA_OFFLINE') {
      message = 'Локальная модель (Ollama) не запущена. Откройте terminal и запустите «ollama serve», затем «ollama pull «. Подробности — в README.';
    } else if (code === 'MODEL_MISSING') {
      message = 'Модель не загрузилась: ollama pull ' + (this.settings.llm?.model || '');
    }
    this.onStatus('error');
    this.onAssistantDelta('');
    this.onAssistantFinal('⚠️ ' + message, true);
    this.onError(err, message);
  }

  /* -------------------------------------------------------------------- voice in */
  async handlePhrase(blob) {
    if (this.busy && !this.listening) {
      // already answering; the barge-in path handles interruption separately
    }
    this.onStatus('thinking');
    try {
      const form = new FormData();
      form.append('audio', blob, 'speech.webm');
      const response = await fetch('/api/stt', { method: 'POST', body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || 'STT ошибка');
      if (!data.text) {
        this.onToast('Не распознала речь — попробуйте ещё раз', 'error');
        if (this.listening) this.onStatus('listening');
        return;
      }
      await this.sendText(data.text, 'voice');
    } catch (err) {
      this.onToast(err.message || 'Не удалось распознать', 'error');
      this.onStatus(this.listening ? 'listening' : 'idle');
    }
  }
}

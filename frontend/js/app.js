/**
 * Application wiring: avatar, microphone, TTS, conversation, UI.
 */

import { api } from './api.js';
import { Avatar } from './avatar.js';
import { FaceTracker } from './tracker.js';
import { Microphone } from './mic.js';
import { TtsPlayer, unlockAudio } from './tts.js';
import { Conversation } from './chat.js';
import { initSettings, loadFaceOverride } from './settings.js';

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
  idle: 'Ожидание',
  listening: 'Слушает',
  thinking: 'Думает',
  speaking: 'Говорит',
  error: 'Ошибка',
};

const state = {
  status: 'idle',
  config: null,
  settings: {},
  sound: localStorage.getItem('ai-companion.sound') !== '0',
  avatar: null,
  player: null,
  mic: null,
  conversation: null,
};

function toast(message, kind = '') {
  const host = $('toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .3s';
    setTimeout(() => el.remove(), 320);
  }, 3600);
}

function setStatus(next, detail = '') {
  state.status = next;
  $('stage-badge').dataset.state = next;
  $('stage-badge-text').textContent = STATUS_TEXT[next] || next;
  $('stage').dataset.state = next;
  const hints = {
    idle: 'Нажмите «Голосом», чтобы поговорить',
    listening: 'Слушаю… говорите',
    thinking: 'Думаю над ответом…',
    speaking: 'Отвечаю…',
    error: 'Ошибка — смотрите описание ниже',
  };
  $('stage-hint').textContent = detail || hints[next] || '';
  if (state.avatar) state.avatar.setStatus(next);
  if (next === 'idle' || next === 'listening') {
    $('btn-stop').disabled = !(state.player && state.player.playing);
  } else {
    $('btn-stop').disabled = false;
  }
  updateMeter(next === 'listening' || next === 'speaking');
}

function updateMeter(active) {
  document.querySelector('.voice-meter').style.opacity = active ? '1' : '0.45';
}

/* ------------------------------------------------------------------ rendering */
function addMessage(role, text, options = {}) {
  const host = $('messages');
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}${options.streaming ? ' streaming' : ''}`;
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = role === 'user' ? 'Вы' : state.settings.persona?.name || 'Персонаж';
  const bubble = document.createElement('div');
  bubble.className = `bubble${options.error ? ' error' : ''}`;
  bubble.textContent = text;
  wrap.appendChild(who);
  wrap.appendChild(bubble);
  host.appendChild(wrap);
  if (!options.streaming) {
    host.scrollTop = host.scrollHeight;
  }
  return { wrap, bubble };
}

function scrollDown() {
  const host = $('messages');
  if (host.scrollHeight - host.scrollTop - host.clientHeight < 120) {
    host.scrollTop = host.scrollHeight;
  }
}

/* ------------------------------------------------------------------ draw action */
function openDrawer(id) {
  $('drawer-backdrop').hidden = false;
  $(id).hidden = false;
}

function closeDrawers() {
  $('drawer-backdrop').hidden = true;
  $('drawer-memory').hidden = true;
  $('drawer-settings').hidden = true;
}

/* ---------------------------------------------------------------------- boot */
async function boot() {
  try {
    state.config = await api.get('/api/config');
  } catch (err) {
    toast('Не удалось получить конфигурацию: ' + err.message, 'error');
    return;
  }
  state.settings = {
    persona: { name: state.config.defaults?.persona?.name || 'Агата' },
    voice: { rate: 1, volume: 1 },
    listening: { silence_ms: 700, min_speech_ms: 220, barge_in_db: -16 },
    llm: {},
  };

  $('avatar-name').textContent = state.settings.persona.name;
  $('sound-label').textContent = state.sound ? 'Звук' : 'Без звука';
  $('btn-sound').setAttribute('aria-pressed', String(state.sound));

  await loadAvatar();
  await loadSettings();

  const avatar = state.avatar;
  const player = new TtsPlayer({
    onStart: () => setStatus('speaking'),
    onEnd: () => {
      avatar.clearSpeech();
      if (!state.conversation?.listening && state.status === 'speaking') setStatus('idle');
    },
    onError: (err) => toast('Ошибка речи: ' + err.message, 'error'),
  });
  state.player = player;

  const meter = document.querySelector('.voice-meter');
  const bars = Array.from(meter.querySelectorAll('span'));
  const mic = new Microphone({
    onLevel: (rms) => {
      const active = Math.min(10, Math.max(0, Math.round(rms * 14)));
      for (let i = 0; i < bars.length; i++) {
        const lit = i < active;
        const taper = 1 - (i / bars.length) * 0.55;
        bars[i].style.height = `${lit ? 4 + 22 * taper : 4}px`;
        bars[i].style.background = lit ? 'linear-gradient(180deg,#7fd1ff,#b48cff)' : 'rgba(255,255,255,0.10)';
      }
    },
    onPhraseStart: () => {
      avatar.setEmotion('listening');
      meter.style.opacity = '1';
    },
    onPhraseEnd: (blob) => state.conversation?.handlePhrase(blob),
    onBargeIn: () => {
      state.conversation.interrupt('barge-in');
      setStatus('listening', 'Перебиваю…');
    },
    onError: (err) => {
      toast(describeMicError(err), 'error');
      setStatus('error');
      state.conversation.stopVoice();
    },
    getPlaybackLevel: () => (state.player && state.player.playing ? state.player.getLevel() : 0),
  });
  state.mic = mic;

  const conversation = new Conversation({
    avatar,
    player,
    mic,
    settings: state.settings,
    soundEnabled: state.sound,
    onStatus: setStatus,
    onUserMessage: (text, source) => {
      addMessage('user', text);
      if (source === 'voice' && state.sound) {
        // voice input is echoed as text only
      }
    },
    onAssistantStart: () => {},
    onAssistantDelta: (text) => {
      const last = $('messages').lastElementChild;
      if (last && last.classList.contains('assistant')) {
        last.querySelector('.bubble').textContent = text;
        scrollDown();
        return;
      }
      addMessage('assistant', text, { streaming: true });
    },
    onAssistantFinal: (text, isError) => {
      const last = $('messages').lastElementChild;
      if (last && last.classList.contains('assistant')) {
        last.classList.remove('streaming');
        last.querySelector('.bubble').textContent = text;
      } else if (text) {
        addMessage('assistant', text, { error: isError });
      }
      scrollDown();
    },
    onError: (err, message) => toast(message || err.message, 'error'),
    onToast: toast,
  });
  state.conversation = conversation;

  // ---- controls
  $('btn-mic').addEventListener('click', () => {
    if (state.conversation.listening) {
      state.conversation.stopVoice();
      $('btn-mic').setAttribute('aria-pressed', 'false');
      setStatus(state.status === 'error' ? 'error' : 'idle');
    } else {
      state.conversation.startVoice();
      $('btn-mic').setAttribute('aria-pressed', 'true');
      setStatus('listening');
    }
  });

  $('btn-stop').addEventListener('click', () => {
    state.conversation.interrupt('manual stop');
    setStatus(state.conversation.listening ? 'listening' : 'idle', 'Ответ прерван');
  });

  $('btn-end').addEventListener('click', () => state.conversation.endConversation());

  $('btn-sound').addEventListener('click', () => {
    state.sound = !state.sound;
    localStorage.setItem('ai-companion.sound', state.sound ? '1' : '0');
    $('sound-label').textContent = state.sound ? 'Звук' : 'Без звука';
    $('btn-sound').setAttribute('aria-pressed', String(state.sound));
    if (!state.sound && state.player?.playing) {
      state.player.stop('muted');
      state.avatar.clearSpeech();
    }
    toast(state.sound ? 'Звук включён' : 'Звук выключен', 'ok');
  });

  $('btn-memory').addEventListener('click', () => {
    openDrawer('drawer-memory');
    refreshMemory();
  });
  $('btn-settings').addEventListener('click', () => openDrawer('drawer-settings'));
  $('drawer-backdrop').addEventListener('click', closeDrawers);
  $('btn-memory-close').addEventListener('click', closeDrawers);
  $('btn-settings-close').addEventListener('click', closeDrawers);
  $('btn-clear-history').addEventListener('click', async () => {
    if (!confirm('Очистить историю диалога?')) return;
    try {
      await api.del('/api/memory/history');
      $('messages').innerHTML = '';
      toast('История очищена', 'ok');
      refreshMemory();
    } catch (err) {
      toast(err.message, 'error');
    }
  });
  $('btn-new-conversation').addEventListener('click', () => {
    state.conversation.interrupt('new conversation');
    $('messages').innerHTML = '';
    setStatus('idle');
  });

  // ---- composer
  const composer = $('composer');
  const input = $('composer-input');
  const send = composer.querySelector('.send');
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(132, input.scrollHeight)}px`;
    send.disabled = !input.value.trim();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  composer.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.style.height = 'auto';
    send.disabled = true;
    state.conversation.sendText(text, 'text');
  });

  // ---- memory drawer
  $('mem-save-toggle').addEventListener('change', async (event) => {
    try {
      await api.post('/api/memory/save', { save: event.target.checked });
      toast(event.target.checked ? 'Сохранение включено' : 'Сохранение выключено', 'ok');
      refreshMemory();
    } catch (err) {
      toast(err.message, 'error');
      event.target.checked = !event.target.checked;
    }
  });
  $('btn-memory-clear-history').addEventListener('click', async () => {
    try {
      await api.del('/api/memory/history');
      toast('История очищена', 'ok');
      refreshMemory();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  // ---- keyboard
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeDrawers();
    if (event.key === ' ' && event.target === document.body && !state.conversation?.busy) {
      event.preventDefault();
      $('btn-mic').click();
    }
  });

  setStatus('idle');
  startStatusPolling();
}

async function loadAvatar() {
  const avatar = new Avatar($('avatar'), {
    getLevel: () => (state.player && state.player.playing ? state.player.getLevel() : 0),
  });
  try {
    await avatar.load(
      state.config.portrait || '/assets/avatar.png',
      state.config.face_config || '/assets/face.json',
      loadFaceOverride()
    );
  } catch (err) {
    toast('Не удалось загрузить портрет: ' + err.message, 'error');
    return;
  }
  state.avatar = avatar;
  avatar.setVolume(state.settings.voice.volume);
  avatar.start();

  const trackerMode = localStorage.getItem('ai-companion.tracker') || 'auto';
  if (trackerMode !== 'off' && state.config.face_model) {
    try {
      const tracker = await FaceTracker.create(state.config.face_model, '/vendor/wasm', 'GPU');
      if (tracker) {
        avatar.attachTracker(tracker);
        avatar.setTrackerEnabled(trackerMode !== 'off' && $('set-eyetrack')?.checked !== false);
        setInterval(() => {
          if (!avatar.trackerEnabled) return;
          const signal = tracker.track(avatar.canvas, (x, y) => avatar.toCanvas(x, y), performance.now());
          avatar.trackerSignal = signal;
        }, 66);
      } else {
        console.info('Слежение за взглядом недоступно — используется анимация по умолчанию');
      }
    } catch (err) {
      console.info('Слежение за взглядом недоступно:', err.message);
    }
  }
}

async function loadSettings() {
  const data = await api.get('/api/settings');
  state.settings = Object.assign(state.settings, data.settings);
  $('avatar-name').textContent = state.settings.persona?.name || 'AI Companion';
  await initSettings({
    onChange: async (payload) => {
      Object.assign(state.settings, flattenSettings(payload));
      try {
        await api.put('/api/settings', payload);
        toast('Настройки сохранены', 'ok');
      } catch (err) {
        toast(err.message, 'error');
      }
    },
    getAvatar: () => state.avatar,
    toast,
  });
}

function flattenSettings(payload) {
  const out = {};
  for (const [key, value] of Object.entries(payload)) {
    const [section, item] = key.split('.');
    out[section] = out[section] || {};
    out[section][item] = value;
  }
  return out;
}

async function refreshMemory() {
  const data = await api.get('/api/memory');
  $('mem-save-toggle').checked = !!data.status?.save_enabled;
  $('memory-stats').textContent = `Сообщений: ${data.status?.messages ?? 0} · Фактов: ${data.status?.facts ?? 0}`;
  $('facts-count').textContent = String((data.facts || []).length);
  $('history-count').textContent = String((data.messages || []).length);

  const factList = $('fact-list');
  factList.innerHTML = '';
  for (const fact of data.facts || []) {
    const li = document.createElement('li');
    const text = document.createElement('span');
    text.className = 'fact-text';
    text.textContent = `[${fact.subject}] ${fact.content}`;
    const del = document.createElement('button');
    del.className = 'list-del';
    del.textContent = 'Удалить';
    del.addEventListener('click', async () => {
      await api.del(`/api/memory/facts/${fact.id}`);
      refreshMemory();
    });
    li.appendChild(text);
    li.appendChild(del);
    factList.appendChild(li);
  }

  const historyList = $('history-list');
  historyList.innerHTML = '';
  for (const message of (data.messages || []).slice(-40).reverse()) {
    const li = document.createElement('li');
    const line = document.createElement('div');
    line.className = 'msg-line';
    line.textContent = `${message.role === 'user' ? 'Вы' : 'Персонаж'}: ${message.content}`;
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = `#${message.id} · ${message.source}`;
    const del = document.createElement('button');
    del.className = 'list-del';
    del.textContent = 'Удалить';
    del.addEventListener('click', async () => {
      await api.del(`/api/memory/messages/${message.id}`);
      refreshMemory();
    });
    li.appendChild(line);
    li.appendChild(meta);
    li.appendChild(del);
    historyList.appendChild(li);
  }
}

async function startStatusPolling() {
  const render = async () => {
    try {
      const status = await api.get('/api/status');
      const line = $('connection-line');
      const ollama = status.ollama || {};
      if (ollama.reachable && ollama.available) {
        line.textContent = `Ollama · ${ollama.model}`;
        line.className = 'brand-sub ok';
      } else if (ollama.reachable) {
        line.textContent = `Ollama запущена, модель не загрузилась: ${ollama.model}`;
        line.className = 'brand-sub warn';
      } else {
        line.textContent = 'Ollama не запущена — текстовый диалог недоступен';
        line.className = 'brand-sub err';
      }
      if (status.stt && !status.stt.ready) {
        toast(`STT не готова: ${status.stt.error}`, 'error');
      }
      if (status.tts && !status.tts.ready) {
        toast(`TTS не готова: ${status.tts.error}`, 'error');
      }
    } catch (err) {
      console.warn('Ошибка опроса статуса', err);
    }
  };
  render();
  setInterval(render, 20000);
}

function describeMicError(err) {
  if (err && err.name === 'NotAllowedError') return 'Доступ к микрофону запрещён — разрешите в настройках браузера';
  if (err && err.name === 'NotFoundError') return 'Микрофон не найден';
  if (err && err.name === 'NotReadableError') return 'Микрофон занят другим приложением';
  return err?.message || 'Не удалось получить микрофон';
}

// Unlock AudioContext on first user gesture — required for Piper TTS in Safari
const _unlockOnce = () => {
  unlockAudio();
  window.removeEventListener('click', _unlockOnce);
  window.removeEventListener('keydown', _unlockOnce);
  window.removeEventListener('touchstart', _unlockOnce);
};
window.addEventListener('click', _unlockOnce, { once: true });
window.addEventListener('keydown', _unlockOnce, { once: true });
window.addEventListener('touchstart', _unlockOnce, { once: true });

boot().catch((err) => {
  console.error(err);
  toast('Не удалось запустить: ' + err.message, 'error');
});

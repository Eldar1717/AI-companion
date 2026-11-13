(() => {
  'use strict';

  const STORAGE_KEY = 'ai-companion-history-v1';
  const MAX_SAVED_MESSAGES = 60;
  const $ = (id) => document.getElementById(id);

  const elements = {
    messages: $('messages'), welcome: $('welcome-message'),
    form: $('message-form'), input: $('message-input'), send: $('send-button'),
    mic: $('mic-button'), micHint: $('mic-hint'),
    avatar: $('avatar-card'), avatarState: $('avatar-state'),
    tagline: $('avatar-tagline'), typing: $('typing-state'), typingLabel: $('typing-label'),
    quickPrompts: $('quick-prompts'), connection: $('connection-label'),
    systemFoot: $('system-foot'), soundToggle: $('sound-toggle'),
    stopSpeaking: $('stop-speaking'), clearHistory: $('clear-history'),
    voiceSelect: $('voice-select'), voiceRate: $('voice-rate'),
    toast: $('toast')
  };

  let history = loadHistory();
  let isBusy = false;
  let isRecording = false;
  let recognition = null;
  let finalTranscript = '';
  let toastTimer = null;
  let selectedVoice = null;
  let speechEnabled = true;
  let speechRate = 1.0;
  let availableRussianVoices = [];

  function loadHistory() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((item) => item && ['user', 'assistant'].includes(item.role) && typeof item.content === 'string')
        .slice(-MAX_SAVED_MESSAGES);
    } catch (_) {
      return [];
    }
  }

  function saveHistory() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-MAX_SAVED_MESSAGES)));
    } catch (_) {
      showToast('Не удалось сохранить историю в браузере.');
    }
  }

  function formatTime(timestamp) {
    if (!timestamp) return 'сейчас';
    return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date(timestamp));
  }

  function addMessage(role, content, persist = true) {
    const message = { role, content, timestamp: Date.now() };
    history.push(message);
    if (history.length > MAX_SAVED_MESSAGES) history = history.slice(-MAX_SAVED_MESSAGES);
    renderMessage(message);
    if (persist) saveHistory();
    return message;
  }

  function renderMessage(message) {
    if (elements.welcome) elements.welcome.remove();
    const row = document.createElement('div');
    row.className = `message-row ${message.role}`;

    if (message.role === 'assistant') {
      const avatar = document.createElement('div');
      avatar.className = 'message-avatar';
      const image = document.createElement('img');
      image.src = '/static/assets/avatar.png';
      image.alt = '';
      avatar.appendChild(image);
      row.appendChild(avatar);
    }

    const copy = document.createElement('div');
    copy.className = 'message-copy';
    const author = document.createElement('div');
    author.className = 'message-author';
    author.textContent = message.role === 'assistant' ? 'Твоя спутница' : 'Ты';
    const bubble = document.createElement('div');
    bubble.className = `message-bubble ${message.role === 'assistant' ? 'assistant-bubble' : ''}`;
    bubble.appendChild(document.createTextNode(message.content));
    const time = document.createElement('span');
    time.className = 'message-time';
    time.textContent = formatTime(message.timestamp);
    bubble.appendChild(time);
    copy.append(author, bubble);
    row.appendChild(copy);

    if (message.role === 'user') {
      const avatar = document.createElement('div');
      avatar.className = 'message-avatar user-avatar';
      avatar.textContent = 'Я';
      row.appendChild(avatar);
    }

    elements.messages.appendChild(row);
    scrollToBottom();
  }

  function renderHistory() {
    elements.messages.innerHTML = '';
    if (!history.length) {
      elements.messages.appendChild(elements.welcome);
      elements.welcome.hidden = false;
      return;
    }
    history.forEach(renderMessage);
  }

  function scrollToBottom() {
    requestAnimationFrame(() => { elements.messages.scrollTop = elements.messages.scrollHeight; });
  }

  function setAvatarState(state, label) {
    elements.avatar.classList.remove('listening', 'thinking', 'speaking');
    if (state !== 'idle') elements.avatar.classList.add(state);
    elements.avatarState.textContent = label;
    const taglines = {
      idle: 'Я рядом. Давай поговорим.',
      listening: 'Я внимательно тебя слушаю…',
      thinking: 'Секунду, формулирую ответ…',
      speaking: 'Мне приятно с тобой общаться.'
    };
    elements.tagline.textContent = taglines[state] || taglines.idle;
  }

  function setTyping(visible, text = 'Подбираю слова...') {
    elements.typing.classList.toggle('visible', visible);
    elements.typingLabel.textContent = text;
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.classList.add('visible');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => elements.toast.classList.remove('visible'), 4200);
  }

  async function checkConnection() {
    try {
      const response = await fetch('/api/status', { cache: 'no-store' });
      const data = await response.json();
      $('model-name').textContent = data.model || 'Qwen3 4B';
      if (data.ok) {
        elements.connection.textContent = 'Подключено';
        elements.connection.className = 'connection-ok';
        elements.systemFoot.textContent = 'Локальная модель готова к разговору';
      } else if (!data.ollama_running) {
        elements.connection.textContent = 'Нет Ollama';
        elements.connection.className = 'connection-bad';
        elements.systemFoot.textContent = 'Запусти Ollama и обнови страницу';
      } else {
        elements.connection.textContent = 'Нужна модель';
        elements.connection.className = 'connection-wait';
        elements.systemFoot.textContent = `Установи: ollama pull ${data.model}`;
      }
    } catch (_) {
      elements.connection.textContent = 'Нет связи';
      elements.connection.className = 'connection-bad';
      elements.systemFoot.textContent = 'Проверь, что локальный сервер запущен';
    }
  }

  function setBusy(value) {
    isBusy = value;
    elements.send.disabled = value;
    elements.mic.disabled = value;
    elements.input.disabled = value;
    elements.send.innerHTML = value
      ? '<span class="send-spinner" aria-hidden="true"></span>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m21 3-7.2 18-3.7-7.1L3 10.2 21 3Z"/><path d="m10.1 13.9 4.3-4.3"/></svg>';
  }

  function stopSpeech() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    setAvatarState(isRecording ? 'listening' : 'idle', isRecording ? 'Слушаю...' : 'На связи');
  }

  function speak(text) {
    if (!speechEnabled || !('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = selectedVoice?.lang || 'ru-RU';
    if (selectedVoice) utterance.voice = selectedVoice;
    utterance.rate = speechRate;
    utterance.pitch = 1.04;
    utterance.onstart = () => setAvatarState('speaking', 'Говорю...');
    utterance.onend = () => setAvatarState(isRecording ? 'listening' : 'idle', isRecording ? 'Слушаю...' : 'На связи');
    utterance.onerror = () => setAvatarState('idle', 'На связи');
    window.speechSynthesis.speak(utterance);
  }

  function setupVoices() {
    if (!('speechSynthesis' in window)) return;
    const voices = window.speechSynthesis.getVoices();
    availableRussianVoices = voices.filter((voice) => /^ru([_-]|$)/i.test(voice.lang));
    const preferred = availableRussianVoices.find((voice) => /female|милена|алёна|алена|ирина|katya|svetlana/i.test(voice.name)) || availableRussianVoices[0];
    const currentUri = selectedVoice?.voiceURI || '';
    selectedVoice = availableRussianVoices.find((voice) => voice.voiceURI === currentUri) || preferred || null;
    const previousValue = elements.voiceSelect.value;
    elements.voiceSelect.innerHTML = '';
    const automatic = document.createElement('option');
    automatic.value = '';
    automatic.textContent = 'Автовыбор · русский';
    elements.voiceSelect.appendChild(automatic);
    availableRussianVoices.forEach((voice) => {
      const option = document.createElement('option');
      option.value = voice.voiceURI;
      option.textContent = `${voice.name} (${voice.lang})`;
      elements.voiceSelect.appendChild(option);
    });
    if (previousValue && availableRussianVoices.some((voice) => voice.voiceURI === previousValue)) {
      elements.voiceSelect.value = previousValue;
      selectedVoice = availableRussianVoices.find((voice) => voice.voiceURI === previousValue) || selectedVoice;
    } else {
      elements.voiceSelect.value = '';
    }
    if (availableRussianVoices.length === 0) {
      elements.voiceSelect.disabled = true;
      automatic.textContent = 'Системный голос · ru-RU';
    } else {
      elements.voiceSelect.disabled = false;
    }
  }

  async function sendMessage(rawText) {
    const text = String(rawText || '').trim();
    if (!text || isBusy) return;
    stopSpeech();
    if (recognition && isRecording) {
      try { recognition.stop(); } catch (_) { /* ignore */ }
      isRecording = false;
      elements.mic.classList.remove('recording');
    }

    addMessage('user', text);
    elements.input.value = '';
    resizeInput();
    elements.quickPrompts.hidden = true;
    setBusy(true);
    setTyping(true, 'Думаю над ответом...');
    setAvatarState('thinking', 'Думаю...');

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history.slice(-14).map(({ role, content }) => ({ role, content })) })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `Ошибка сервера (${response.status})`);
      addMessage('assistant', data.reply);
      setTyping(false);
      setAvatarState('idle', 'На связи');
      speak(data.reply);
    } catch (error) {
      setTyping(false);
      setAvatarState('idle', 'На связи');
      const errorText = error?.message || 'Не удалось связаться с ИИ-моделью.';
      addMessage('assistant', `Похоже, сейчас не получается подключиться к моему ИИ-модулю. ${errorText}`);
      showToast(errorText);
      checkConnection();
    } finally {
      setBusy(false);
      elements.input.focus();
    }
  }

  function getRecognitionConstructor() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function startRecognition() {
    const Recognition = getRecognitionConstructor();
    if (!Recognition) {
      showToast('В этом браузере голосовой ввод не поддерживается. Открой проект в актуальном Google Chrome или используй поле текста.');
      elements.micHint.textContent = 'Голосовой ввод не поддерживается этим браузером';
      return;
    }
    if (isBusy) return;
    if (isRecording && recognition) {
      recognition.stop();
      return;
    }

    stopSpeech();
    finalTranscript = '';
    recognition = new Recognition();
    recognition.lang = 'ru-RU';
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      isRecording = true;
      elements.mic.classList.add('recording');
      elements.mic.setAttribute('aria-label', 'Остановить запись');
      elements.micHint.textContent = 'Слушаю тебя… нажми на микрофон для остановки';
      setAvatarState('listening', 'Слушаю...');
    };

    recognition.onresult = (event) => {
      let interim = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const phrase = event.results[index][0].transcript;
        if (event.results[index].isFinal) finalTranscript += `${phrase} `;
        else interim += phrase;
      }
      elements.micHint.textContent = interim ? `Распознано: ${interim}` : 'Слушаю тебя…';
    };

    recognition.onerror = (event) => {
      const messages = {
        'not-allowed': 'Разреши доступ к микрофону в настройках браузера.',
        'no-speech': 'Не удалось услышать речь. Попробуй ещё раз.',
        'network': 'Служба распознавания речи недоступна. Проверь интернет.',
        'audio-capture': 'Не найден микрофон. Проверь подключение устройства.'
      };
      showToast(messages[event.error] || `Ошибка голосового ввода: ${event.error}`);
    };

    recognition.onend = () => {
      const transcript = finalTranscript.trim();
      isRecording = false;
      elements.mic.classList.remove('recording');
      elements.mic.setAttribute('aria-label', 'Начать голосовой ввод');
      elements.micHint.textContent = 'Нажми на микрофон и говори';
      setAvatarState('idle', 'На связи');
      if (transcript) sendMessage(transcript);
    };

    try {
      recognition.start();
    } catch (error) {
      isRecording = false;
      elements.mic.classList.remove('recording');
      showToast('Не удалось запустить микрофон. Попробуй обновить страницу.');
    }
  }

  function resizeInput() {
    elements.input.style.height = 'auto';
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 110)}px`;
  }

  elements.form.addEventListener('submit', (event) => {
    event.preventDefault();
    sendMessage(elements.input.value);
  });

  elements.input.addEventListener('input', resizeInput);
  elements.input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      elements.form.requestSubmit();
    }
  });

  elements.mic.addEventListener('click', startRecognition);
  elements.stopSpeaking.addEventListener('click', stopSpeech);
  elements.voiceSelect.addEventListener('change', () => {
    selectedVoice = availableRussianVoices.find((voice) => voice.voiceURI === elements.voiceSelect.value) || null;
  });
  elements.voiceRate.addEventListener('change', () => {
    speechRate = Number(elements.voiceRate.value) || 1;
  });

  elements.soundToggle.addEventListener('click', () => {
    speechEnabled = !speechEnabled;
    elements.soundToggle.classList.toggle('active', speechEnabled);
    elements.soundToggle.setAttribute('aria-pressed', String(speechEnabled));
    elements.soundToggle.querySelector('span').textContent = speechEnabled ? 'Голос включён' : 'Голос выключен';
    if (!speechEnabled) stopSpeech();
  });

  elements.clearHistory.addEventListener('click', () => {
    const accepted = window.confirm('Очистить историю диалога в этом браузере?');
    if (!accepted) return;
    stopSpeech();
    history = [];
    saveHistory();
    renderHistory();
    elements.quickPrompts.hidden = false;
    showToast('История диалога очищена.');
  });

  elements.quickPrompts.addEventListener('click', (event) => {
    const button = event.target.closest('[data-prompt]');
    if (button) sendMessage(button.dataset.prompt);
  });

  // Browser speech voices may arrive asynchronously.
  if ('speechSynthesis' in window) {
    setupVoices();
    window.speechSynthesis.addEventListener?.('voiceschanged', setupVoices);
    window.speechSynthesis.onvoiceschanged = setupVoices;
  }

  renderHistory();
  if (history.length) elements.quickPrompts.hidden = true;
  checkConnection();
  window.setInterval(checkConnection, 30000);
})();

# AI Companion — локальный голосовой ИИ-компаньон (v2)

Персональный веб-аватар, который живёт на вашем компьютере: общается на русском языке
через локальную языковую модель, «слушает» микрофон, озвучивает ответы локальным
синтезатором речи и реагирует выражением лица на то, что происходит в разговоре.
Никакие ключевые токены, подписки и сторонние облачные API не используются.

## Что умеет

- **Голосовой цикл**: микрофон → локальный Whisper (STT) → Ollama (LLM) → локальный
  Piper (TTS) → анимированный портрет.
- **Перебор** (barge-in): нажмите микрофонную кнопку во время речи компаньона —
  он остановится и начнёт слушать вас.
- **Потоковый чат**: токены arrive по мере генерации (SSE), текст появляется
  посимвольно.
- **Аватар-портрет** с зонами для век, губ и бровей: они двигаются, когда компаньон
  говорит, молчит или «слушает». Landmarks раскрашиваются через локальный MediaPipe
  bundle (без CDN). Зоны можно отредактировать в «Параметры аватара».
- **Память**: важная информация из разговора сохраняется в SQLite (`data/memory.db`),
  показывается в panel «Память», можно редактировать и удалять.
- **Параметры**: голос и скорость Piper, личность (prompt), модель Ollama,
  СОС→динамики, STT-модель, токены API, длина контекста.
- **Безопасность**: Origin/Referer-проверка, защита от path traversal, токены.

## Требования

- **macOS / Linux / Windows** (графический интерфейс — любой браузер).
- **Python 3.11+**.
- **Ollama** (https://ollama.com) с русскоязычной моделью. Установите и запустите
  `ollama serve`, затем поднимите модель:

  ```bash
  ollama pull qwen3.5:4b     # или любую другую русскоязычную модель
  ```

- Некоторые Piper-голосы (например, `ru_RU-irina-medium`) используют лицензию
  RHVoice — читайте условия при скачивании.

## Установка и запуск

### macOS (один файл)

1. Дважды кликните **`run_mac.command`**. Он создаст виртуальную среду,
   установит зависимости, скачает нужные модели и откроет приложение в браузере.
   Если Ollama ещё не установлен,launcher попросит вас его установить.

### Ручная установка (любая платформа)

```bash
git clone <this repo> && cd ai-companion

python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/download_models.py   # скачивает го́лос Piper и MediaPipe face model
# (можно начать с --list, чтобы посмотреть доступные русские голоса)

python scripts/check_env.py         # проверит окружение и подскажет, что не хватает

ollama serve &                       # если Ollama ещё не запущен
ollama pull qwen3.5:4b

python app.py                        # или python -m waitress --host=127.0.0.1 --port=8850 app:app
```

Откройте `http://127.0.0.1:8850` в браузере.

### Конфигурация

Скопируйте `.env.example` в `.env` (на уровне проекта) и отредактируйте.
Все переменные опциональны:

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `AI_COMPANION_HOST` / `AI_COMPANION_PORT` | адрес и порт сервера | `127.0.0.1:8850` |
| `AI_COMPANION_DATA` / `AI_COMPANION_MODELS` | каталоги данных и моделей | `./data`, `./models` |
| `AI_COMPANION_PIPER_DIRS` | дополнительные каталоги голосов Piper (через `,`) | `./models/piper,./models` |
| `AI_COMPANION_SECRET` | секрет токена API (пусто = доступ только из браузера) | пусто |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | адрес Ollama и модель | `http://127.0.0.1:11434`, `qwen3.5:4b` |
| `AI_COMPANION_STT_MODEL` | размер Whisper (tiny…large-v3) | `small` |
| `AI_COMPANION_STT_DEVICE` / `AI_COMPANION_STT_COMPUTE` | `auto`/`cpu`/`cuda` и `auto`/`int8`/`float32` | `auto`/`auto` |
| `AI_COMPANION_LOG_LEVEL` | уровень логирования | `INFO` |

## HTTP API

| Эндпоинт | Method | Назначение |
|---|---|---|
| `/api/health` | GET | `{"ok": true}` |
| `/api/config` | GET | конфиг браузера (голос, STT-модель, порт) |
| `/api/status` | GET | состояние Ollama/STT/TTS/памяти (не блокирует готовность) |
| `/api/chat` | POST | `{"message","source"}` → SSE-поток (meta/status/token/done) |
| `/api/chat/complete` | POST | получить полный ответ сразу (не потоком) |
| `/api/stt` | POST | multipart wav → `{"text","language"}` |
| `/api/tts` / `/api/tts/stream` | POST | озвучить текст (wav / поток PCMU) |
| `/api/settings` / `/api/settings/personality` | GET/PUT | параметры и личность |
| `/api/memory` / `/api/memory/messages` / `/api/memory/facts` | GET/DELETE | память |
| `/api/tts/voices` | GET | доступные голоса Piper |

### Потоковый чат

```bash
curl -N -X POST http://127.0.0.1:8850/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Привет, как дела?"}'
```

```
data: {"type":"meta","user_message_id":1,"saved":true,"source":"text","facts":[]}
data: {"type":"status","state":"thinking"}
data: {"type":"token","text":"Привет"}
...
data: {"type":"done","message_id":2,"ms":732}
```

### Озвучка

```bash
curl -X POST http://127.0.0.1:8850/api/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Привет! Как я могу помочь?"}' --output out.wav
```

## Архитектура

```text
Браузер ── Web Speech / MediaPipe / Web Audio ──────┐
     │                                            │
     │ /api/* (HTTP + SSE)                         │
     ▼                                            │
Flask (waitress)  ── app.py ──────────────────────┘
     │
     ├── backend/api/        маршруты (chat, tts, stt, memory, settings, system)
     ├── backend/services/   ollama.py  ·  chat.py  ·  tts.py  ·  stt.py
     ├── backend/models/     database.py · memory.py
     ├── backend/avatar/     analyze.py  →  landmarks → frontend/assets/face.json
     ├── backend/security.py  Origin/Referer-проверка, path traversal-защита
     └── frontend/           index.html · css/ · js/ (api, mic, tts, chat, avatar, tracker)
```

- **Chat runner** посылает запрос в Ollama с потоковым NDJSON и пропускает токены
  через `chat_history` (последние N сообщений + ограничение по символам) и
  `personality`.
- **TTS** использует Piper (воспроизводит NANN/AlignedTTS-голоса через piper-tts
  Python-бинды); изначальная настройка голосов — каталог Piper + Web Speech API.
- **STT** — faster-whisper, начинает модель на первом запросе (или при запуске,
  в фоне).
- **Память** — SQLite; из сообщений извлекаются факты, пользователь может редактировать
  и удалять их.
- **Аватар** — портрет из `frontend/assets/avatar.png`; MediaPipe face landmarks
  превращаются в зоны век/губ/бровей (`face.json`), JS-движок двигает их в зависимости
  от состояния (слушает / говорит / спокоен).

## Тесты

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

Охватывает: безопасность (traversal, Origin, заголовки), настройки/личность,
память, TTS (sanitize, phonemes, voices, wav), STT (декодирование, energy-gate,
признавание), зоны лица, HTTP-API (mock Ollama в тесте). Тесты, которые требуют
великих моделей (Piper/Whisper), пропускаются автоматически, если модели не
скачаны.

## Обновление и очистка

- Обновить зависимости: `pip install -r requirements.txt`.
- Сбросить память: удалить `data/memory.db`.
- Сбросить параметры: удалить `data/settings.json`.
- Бэкап памяти: copy `data/memory.db`.
- Старая версия (v1) сохранена в `legacy-v1/` и не используется.

## Устранение неисправностей

| Проблема | Решение |
|---|---|
| `ollama: not found` | Установите Ollama и запустите `ollama serve`; проверьте `ollama pull qwen3.5:4b`. |
| Порт занят | Задайте `AI_COMPANION_PORT` в `.env`. |
| `TTS not available` | Скачайте голос: `python scripts/download_models.py`. |
| Нет landmarks / лицо не двигается | Скачайте `face_landmarker.task` (script поможет), перезапустите приложение. |
| Голосовой ввод не работает | Разрешите доступ к микрофону в настройках браузера; проверьте `check_env.py`. |
| `OOM` при больших Whisper-моделях | Используйте `AI_COMPANION_STT_MODEL=small` или `tiny`. |
| Требуется доступ с другого компьютера | Задайте `AI_COMPANION_SECRET` и используйте заголовок `X-API-Token`. |

## Лицензии

- Piper (голоса): RHVoice — см. условия при скачивании.
- MediaPipe Face Landmarker: Apache-2.0 (bundled locally in `frontend/vendor/`).

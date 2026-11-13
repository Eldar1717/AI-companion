/**
 * Settings panel: voice, perception, animation, persona prompt and the
 * avatar-region editor (drag the eye/mouth boxes if auto-detection is off).
 */

import { api } from './api.js';
import { computeTransform } from './avatar.js';

const $ = (id) => document.getElementById(id);

const REGION_COLORS = {
  right_eye: '#6fc7ff',
  left_eye: '#b48cff',
  mouth: '#57d9a3',
};

const REGIONS = ['right_eye', 'left_eye', 'mouth'];

export class RegionEditor {
  constructor(canvas, face, image, onChange) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.face = face;
    this.image = image;
    this.onChange = onChange;
    this.cw = canvas.width;
    this.ch = canvas.height;
    this.drag = null;
    this.override = null;

    canvas.addEventListener('pointerdown', (event) => this._down(event));
    canvas.addEventListener('pointermove', (event) => this._move(event));
    canvas.addEventListener('pointerup', (event) => this._up(event));
    canvas.addEventListener('pointercancel', () => this.drag = null);
  }

  setOverride(override) {
    this.override = override || null;
    if (this.face && this.image) this.layout();
    else this.draw();
  }

  getRegions() {
    const parts = this.face.parts || {};
    return REGIONS.map((name) => ({
      name,
      box: this.override && this.override[name] ? this.override[name].box : parts[name].box,
    }));
  }

  _toCanvas(x, y) {
    return { x: x * this.transform.sx + this.transform.ox, y: y * this.transform.sy + this.transform.oy };
  }

  _toImage(x, y) {
    return { x: (x - this.transform.ox) / this.transform.sx, y: (y - this.transform.oy) / this.transform.sy };
  }

  draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.cw, this.ch);
    if (!this.image || !this.face) return;
    if (!this.transform) this.layout();
    if (!this.transform) return;
    const t = this.transform;
    ctx.drawImage(this.image, t.ox, t.oy, this.face.width * t.sx, this.face.height * t.sy);
    for (const region of this.getRegions()) {
      const a = this._toCanvas(region.box[0], region.box[1]);
      const b = this._toCanvas(region.box[2], region.box[3]);
      const x = Math.min(a.x, b.x);
      const y = Math.min(a.y, b.y);
      const w = Math.abs(b.x - a.x);
      const h = Math.abs(b.y - a.y);
      ctx.save();
      ctx.strokeStyle = REGION_COLORS[region.name];
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      ctx.fillStyle = REGION_COLORS[region.name];
      for (const [hx, hy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) {
        ctx.fillRect(hx - 4, hy - 4, 8, 8);
      }
      ctx.restore();
    }
  }

  layout() {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width) {
      this.cw = rect.width;
      this.ch = rect.height;
    } else {
      this.cw = this.canvas.clientWidth || this.canvas.width;
      this.ch = this.canvas.clientHeight || this.canvas.height;
    }
    // keep the drawing buffer crisp without changing the CSS size
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(this.cw * dpr);
    this.canvas.height = Math.round(this.ch * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.transform = computeTransform(this.face, this.cw, this.ch);
    this.draw();
  }

  _hit(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    for (const region of this.getRegions()) {
      const a = this._toCanvas(region.box[0], region.box[1]);
      const b = this._toCanvas(region.box[2], region.box[3]);
      const minX = Math.min(a.x, b.x);
      const maxX = Math.max(a.x, b.x);
      const minY = Math.min(a.y, b.y);
      const maxY = Math.max(a.y, b.y);
      const handles = [
        [minX, minY], [maxX, minY], [minX, maxY], [maxX, maxY],
      ];
      for (let i = 0; i < handles.length; i++) {
        if (Math.abs(x - handles[i][0]) < 9 && Math.abs(y - handles[i][1]) < 9) {
          return { region: region.name, corner: i, box: [...region.box] };
        }
      }
    }
    return null;
  }

  _down(event) {
    const hit = this._hit(event);
    if (!hit) return;
    this.drag = hit;
    this.canvas.setPointerCapture(event.pointerId);
  }

  _move(event) {
    if (!this.drag) return;
    const rect = this.canvas.getBoundingClientRect();
    const point = this._toImage(event.clientX - rect.left, event.clientY - rect.top);
    const [x0, y0, x1, y1] = this.drag.box;
    let nx0 = x0;
    let ny0 = y0;
    let nx1 = x1;
    let ny1 = y1;
    const minSize = 6;
    if (this.drag.corner === 0) {
      nx0 = Math.min(point.x, x1 - minSize);
      ny0 = Math.min(point.y, y1 - minSize);
    } else if (this.drag.corner === 1) {
      nx1 = Math.max(point.x, x0 + minSize);
      ny0 = Math.min(point.y, y1 - minSize);
    } else if (this.drag.corner === 2) {
      nx0 = Math.min(point.x, x1 - minSize);
      ny1 = Math.max(point.y, y0 + minSize);
    } else {
      nx1 = Math.max(point.x, x0 + minSize);
      ny1 = Math.max(point.y, y0 + minSize);
    }
    this.drag.box = [nx0, ny0, nx1, ny1];
    this.draw();
  }

  _up() {
    if (!this.drag) return;
    const { region, box } = this.drag;
    this.drag = null;
    const parts = this.face.parts || {};
    this.override = Object.assign({}, this.override || {}, {
      [region]: { box: box.map((v) => Math.round(v * 10) / 10), pivot: parts[region].pivot },
    });
    this.onChange(this.override);
    this.draw();
  }
}

export async function initSettings(options) {
  const { onChange, getAvatar, toast } = options;
  const avatar = getAvatar();
  let face = null;
  let faceImage = null;

  const data = await api.get('/api/settings');
  const settings = data.settings || {};

  // ---- voice select
  const voiceSelect = $('set-voice');
  const status = await api.get('/api/status').catch(() => null);
  const voices = (status && status.voices ? status.voices : []).map((v) => v.name);
  for (const name of voices) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    voiceSelect.appendChild(option);
  }
  voiceSelect.value = settings.voice?.voice || 'ru_RU-irina-medium';

  // ---- simple controls
  const controls = [
    ['set-rate', 'voice.rate', 'number', (v) => `${(+v).toFixed(2)}×`],
    ['set-volume', 'voice.volume', 'number', (v) => `${Math.round(+v * 100)}%`],
    ['set-silence', 'listening.silence_ms', 'number', (v) => `${Math.round(v)} мс`],
    ['set-minspeech', 'listening.min_speech_ms', 'number', (v) => `${Math.round(v)} мс`],
    ['set-barge', 'listening.barge_in_db', 'number', (v) => `${Math.round(v)} дБ`],
  ];
  for (const [id, key, type, format] of controls) {
    const input = $(id);
    input.type = 'range';
    input.value = settings[key.split('.')[0]][key.split('.')[1]];
    input.addEventListener('input', () => {
      $(id.replace('set-', 'val-')).textContent = format(input.value);
    });
    input.addEventListener('change', () => {
      const [section, item] = key.split('.');
      onChange({ [key]: type === 'number' ? parseFloat(input.value) : input.value }, section);
    });
  }

  const switches = [
    ['set-animations', 'avatar.animations'],
    ['set-idle', 'avatar.idle'],
    ['set-emotions', 'avatar.emotions'],
    ['set-eyetrack', 'avatar.eye_tracking'],
  ];
  for (const [id, key] of switches) {
    const input = $(id);
    input.checked = !!settings[key.split('.')[0]][key.split('.')[1]];
    input.addEventListener('change', () => {
      onChange({ [key]: input.checked });
      if (avatar) avatar.opts[key.split('.')[1]] = input.checked;
      if (key === 'avatar.eye_tracking' && avatar) avatar.setTrackerEnabled(input.checked);
    });
  }

  const think = $('set-think');
  think.value = String(settings.llm?.think ?? false);
  think.addEventListener('change', () => onChange({ 'llm.think': think.value === 'true' }));

  const nameInput = $('set-persona-name');
  nameInput.value = settings.persona?.name || '';
  nameInput.addEventListener('change', () => onChange({ 'persona.name': nameInput.value }));

  const personality = $('set-personality');
  personality.value = data.personality || '';
  const saveBtn = $('btn-persona-save');
  saveBtn.addEventListener('click', async () => {
    try {
      await api.put('/api/settings/personality', { personality: personality.value });
      const marker = $('persona-saved');
      marker.textContent = 'сохранено';
      setTimeout(() => (marker.textContent = ''), 2500);
      toast('Характер сохранён', 'ok');
    } catch (err) {
      toast(err.message, 'error');
    }
  });
  $('btn-persona-reset').addEventListener('click', async () => {
    const response = await api.get('/api/settings');
    personality.value = response.personality;
    toast('Промпт загружен', 'ok');
  });

  // ---- system info
  $('system-info').textContent = JSON.stringify(
    {
      версия: status?.version || '—',
      Ollama: status?.ollama?.reachable
        ? `${status.ollama.model}${status.ollama.available ? '' : ' (не загружена)'}`
        : 'не запущена',
      STT: status?.stt?.ready ? `Whisper ${status.stt.model}` : `не готова: ${status?.stt?.error || ''}`,
      TTS: status?.tts?.ready ? `Piper ${status.tts.voice}` : `не готова: ${status?.tts?.error || ''}`,
      Память: status?.memory?.save ? 'сохранение включено' : 'сохранение выключено',
      Голоса: voices.length || 'нет',
    },
    null,
    2
  );

  // ---- region editor
  if (avatar) {
    face = avatar.face;
    faceImage = avatar.image;
    const canvas = $('region-canvas');
    const editor = new RegionEditor(canvas, face, faceImage, async (override) => {
      const merged = Object.assign({}, loadFaceOverride(), override);
      localStorage.setItem('ai-companion.face.override', JSON.stringify(merged));
      avatar.applyOverride(merged);
      updateCoords();
    });
    window.__regionEditor = editor;

    editor.setOverride(loadFaceOverride());

    const updateCoords = () => {
      for (const region of REGIONS) {
        const box = editor.getRegions().find((r) => r.name === region).box;
        const el = document.querySelector(`[data-region="${region}"]`);
        if (el) el.textContent = box.map((v) => Math.round(v)).join(', ');
      }
    };
    updateCoords();
    setTimeout(() => {
      editor.layout();
      updateCoords();
    }, 60);

    $('btn-region-reset').addEventListener('click', () => {
      localStorage.removeItem('ai-companion.face.override');
      editor.setOverride(null);
      avatar.applyOverride(null);
      updateCoords();
      toast('Автоопределение восстановлено', 'ok');
    });
  }

  return {
    refresh() {
      return api.get('/api/settings').then((fresh) => {
        personality.value = fresh.personality || '';
        $('system-info').textContent = $('system-info').textContent;
      });
    },
  };
}

export function loadFaceOverride() {
  try {
    return JSON.parse(localStorage.getItem('ai-companion.face.override') || 'null');
  } catch (err) {
    return null;
  }
}

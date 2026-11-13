#!/usr/bin/env python3
"""Browser smoke test for AI Companion (optional development tool).

Requires: ``pip install playwright && python -m playwright install chromium``

Usage::

    python3 scripts/browser_test.py [--port 8850] [--url http://127.0.0.1:8850]

It opens the page in headless Chromium, reports JavaScript errors, checks that
the avatar canvas actually renders and animates, exercises the controls and
takes screenshots into ``data/screenshots``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCREENSHOTS = ROOT / "data" / "screenshots"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AI_COMPANION_URL", "http://127.0.0.1:8850"))
    parser.add_argument("--shots", default=str(SCREENSHOTS))
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright не установлен: pip install playwright && python -m playwright install chromium")
        return 2

    Path(args.shots).mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    status = 0
    try:
        status = _run(args, errors)
    except Exception as exc:  # always report console errors
        print("\n!! неожиданная ошибка теста:", exc)
        status = 1
    finally:
        _report(errors)
    return status


def _run(args, errors):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                  "--ignore-gpu-blocklist", "--autoplay-policy=no-user-gesture-required"]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        try:
            context.grant_permissions(["microphone"])
        except Exception:
            pass
        page = context.new_page()
        requests_seen = []
        page.on("request", lambda r: requests_seen.append(r.url) if "/api/" in r.url else None)
        def on_console(msg):
            if msg.type in ("error", "warning"):
                errors.append(f"console.{msg.type}: {msg.text}")

        page.on("console", on_console)
        page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))

        page.goto(args.url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        print("● страница открыта")

        # ---- canvas must contain the portrait
        stats = page.evaluate(
            """() => {
              const canvas = document.getElementById('avatar');
              const ctx = canvas.getContext('2d');
              const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
              let nonBlank = 0;
              for (let i = 0; i < data.length; i += 4 * 37) {
                if (data[i] + data[i+1] + data[i+2] > 24) nonBlank++;
              }
              return { width: canvas.width, height: canvas.height, samples: Math.floor(data.length / (4*37)), nonBlank };
            }"""
        )
        ratio = stats["nonBlank"] / max(1, stats["samples"])
        print(f"● канвас: {stats['width']}x{stats['height']}, заполнено {ratio:.1%} проверенных точек")
        if ratio < 0.25:
            errors.append(f"канвас почти пуст ({ratio:.1%})")

        # ---- animation: compare two frames
        first = page.evaluate("() => document.getElementById('avatar').toDataURL().slice(-64)")
        page.wait_for_timeout(700)
        second = page.evaluate("() => document.getElementById('avatar').toDataURL().slice(-64)")
        print(f"● анимация: кадры отличаются = {first != second}")
        if first == second:
            errors.append("канвас не обновляется (анимация не работает)")

        page.screenshot(path=str(Path(args.shots) / "01-main.png"))
        print("● снимок: 01-main.png")

        # ---- status line
        print("● статус:", page.inner_text("#connection-line"))

        # ---- open settings drawer
        page.click("#btn-settings")
        page.wait_for_timeout(700)
        print("● панель настроек открыта:", not page.locator("#drawer-settings").is_hidden())
        page.screenshot(path=str(Path(args.shots) / "02-settings.png"))

        # ---- region editor canvas
        has_region = page.evaluate(
            """() => {
              const c = document.getElementById('region-canvas');
              if (!c) return 'нет canvas';
              const ctx = c.getContext('2d');
              const d = ctx.getImageData(0, 0, c.width, c.height).data;
              let n = 0;
              for (let i = 0; i < d.length; i += 4 * 11) if (d[i] + d[i+1] + d[i+2] > 24) n++;
              return `${c.width}x${c.height}, заполнено ${(n / Math.floor(d.length / (4*11))).toFixed(2)}`;
            }"""
        )
        print("● редактор областей:", has_region)
        page.click("#btn-settings-close")

        # ---- memory drawer
        page.click("#btn-memory")
        page.wait_for_timeout(500)
        print("● панель памяти открыта:", not page.locator("#drawer-memory").is_hidden())
        page.click("#btn-memory-close")

        # ---- text message (Ollama may be absent -> error path is expected)
        page.fill("#composer-input", "Привет, как дела?")
        page.click("#composer .send")
        page.wait_for_timeout(6000)
        bubbles = page.locator(".msg").count()
        print(f"● сообщений в диалоге: {bubbles}")
        tts_calls = [u for u in requests_seen if "/api/tts" in u]
        print(f"● запросов TTS: {len(tts_calls)}")
        for url in tts_calls[:4]:
            print("   ", url[:110])
        page.screenshot(path=str(Path(args.shots) / "03-chat.png"))

        # ---- mic button (headless: permission granted, silence -> no phrase)
        page.click("#btn-mic")
        page.wait_for_timeout(600)
        print("● кнопка микрофона:", page.get_attribute("#btn-mic", "aria-pressed"))
        page.screenshot(path=str(Path(args.shots) / "04-listening.png"))
        page.click("#btn-mic")
        page.wait_for_timeout(400)
        page.click("#btn-end")

        page.screenshot(path=str(Path(args.shots) / "05-final.png"))

        if args.keep_open:
            print("● окно оставлено открытым (Ctrl+C для завершения)")
            while True:
                time.sleep(1)
        browser.close()
    return 0


def _report(errors):
    print("\n=== Ошибки консоли / страницы ===")
    if errors:
        for error in errors[:25]:
            print(" -", error)
        return 1
    print("— нет ошибок")
    return 0


if __name__ == "__main__":
    sys.exit(main())

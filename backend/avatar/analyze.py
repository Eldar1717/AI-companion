"""Generate ``face.json`` - the anatomy of the portrait used by the avatar renderer.

The avatar is a *static* image; we "revive" it by cutting out the eyes and the
mouth and animating those regions. This module detects the face with MediaPipe
Face Landmarker and computes, in image coordinates:

* ``face_box``   - the oval of the face (used to frame the portrait in any canvas)
* ``right_eye`` / ``left_eye`` - a box around each eye (upper/lower lids, corners, iris)
* ``mouth``      - a box around the lips

The box corners are padded and clamped so that the cut-out regions never touch the
hairline or the edges of the image. MediaPipe is only needed to *generate* the
file - the browser additionally uses its own MediaPipe session (optional) for
eye-tracking and blink detection.

Usage::

    python -m backend.avatar.analyze frontend/assets/avatar.png
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend import config

log = logging.getLogger(__name__)

# MediaPipe FaceMesh landmark indices used here (468-landmark set + iris).
R_UPPER = [33, 246, 161, 160, 159, 158, 157, 173, 133]
R_LOWER = [33, 7, 163, 144, 145, 153, 154, 155, 133]
R_BROW = [70, 63, 105, 66, 107, 46, 53, 52, 65, 55]
L_UPPER = [362, 398, 384, 385, 386, 387, 388, 466, 263]
L_LOWER = [362, 382, 381, 380, 374, 373, 390, 249, 263]
L_BROW = [300, 293, 334, 296, 336, 276, 283, 282, 295, 285]
R_IRIS = 468
L_IRIS = 473
MOUTH_OUTER_UPPER = [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291]
MOUTH_OUTER_LOWER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291]
MOUTH_INNER_UPPER = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308]
MOUTH_INNER_LOWER = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308]
FACE_OVAL = [
    10, 151, 108, 107, 103, 66, 109, 10, 151, 377, 400, 152, 204, 212, 425, 441, 263,
    280, 377, 387, 362, 251, 284, 117, 287, 356, 345, 175, 148, 176, 149, 150, 176,
    148, 485, 397, 498, 483, 50, 485, 519, 522, 523, 524, 249, 263, 377, 387, 362,
    251, 280, 287, 356, 345, 175, 149, 176, 152, 400, 10, 103, 107, 108, 388, 362,
]


def _bbox(points: Sequence[Sequence[float]], pad: float) -> List[float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    pad_x = (x1 - x0) * pad
    pad_y = (y1 - y0) * pad
    return [x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y]


def _clamp(box: List[float], limits: Sequence[float]) -> List[float]:
    return [max(0.0, box[0]), max(0.0, box[1]), min(limits[0], box[2]), min(limits[1], box[3])]


def _squeeze_into(box: List[float], limits: Sequence[float]) -> List[float]:
    """Keep the box inside the limits without deforming it (centre shift only)."""
    width, height = box[2] - box[0], box[3] - box[1]
    box = [max(0.0, box[0]), max(0.0, box[1]), max(0.0, box[2]), max(0.0, box[3])]
    if width > limits[0]:
        centre = (box[0] + box[2]) / 2
        box[0], box[2] = max(0.0, centre - limits[0] / 2), max(0.0, centre + limits[0] / 2)
    if height > limits[1]:
        centre = (box[1] + box[3]) / 2
        box[1], box[3] = max(0.0, centre - limits[1] / 2), max(0.0, centre + limits[1] / 2)
    # never return an inverted box, even if the input was malformed
    if box[0] > box[2]:
        box[0], box[2] = box[2], box[0]
    if box[1] > box[3]:
        box[1], box[3] = box[3], box[1]
    return box


def build_regions(landmarks: List[Sequence[float]], width: int, height: int,
                  eye_pad: float = 0.16, mouth_pad: float = 0.14) -> Dict[str, Any]:
    limits = [width, height]
    brow_r_bottom = max(landmarks[i][1] for i in R_BROW)
    brow_l_bottom = max(landmarks[i][1] for i in L_BROW)

    right = _bbox([landmarks[i] for i in R_UPPER + R_LOWER + [R_IRIS]], eye_pad)
    left = _bbox([landmarks[i] for i in L_UPPER + L_LOWER + [L_IRIS]], eye_pad)
    mouth = _bbox([landmarks[i] for i in MOUTH_OUTER_UPPER + MOUTH_OUTER_LOWER + MOUTH_INNER_UPPER + MOUTH_INNER_LOWER], mouth_pad)

    # keep the eye boxes below the brow line (hair must never be cut out)
    right[1] = max(right[1], brow_r_bottom + 2.0)
    left[1] = max(left[1], brow_l_bottom + 2.0)

    right = _squeeze_into(_clamp(right, limits), limits)
    left = _squeeze_into(_clamp(left, limits), limits)
    mouth = _squeeze_into(_clamp(mouth, limits), limits)

    def part(box: List[float], pivot_ratio: float = 0.5) -> Dict[str, Any]:
        pivot = [box[0] + (box[2] - box[0]) * pivot_ratio, box[1] + (box[3] - box[1]) * 0.45]
        return {
            "box": [round(box[0], 2), round(box[1], 2), round(box[2], 2), round(box[3], 2)],
            "pivot": [round(pivot[0], 2), round(pivot[1], 2)],
        }

    oval = [i for i in set(FACE_OVAL) if i < len(landmarks)]
    face = _bbox([landmarks[i] for i in oval], pad=0.06)
    face = _squeeze_into(_clamp(face, limits), limits)
    return {
        "width": width,
        "height": height,
        "face_box": [round(v, 2) for v in face],
        "parts": {
            "right_eye": part(right),
            "left_eye": part(left),
            "mouth": part(mouth, pivot_ratio=0.5),
        },
    }


def detect(image_path: Path) -> Optional[List[List[float]]]:
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision
    from PIL import Image

    model = config.MODEL_DIR / "face_landmarker.task"
    if not model.exists():
        log.error("Модель MediaPipe не найдена: %s", model)
        return None
    with Image.open(image_path) as handle:
        width, height = handle.size
        array = np.array(handle.convert("RGB"))
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=array)
    options = vision.FaceLandmarkerOptions(
        base_options=mpp.BaseOptions(model_asset_path=str(model)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        output_face_blendshapes=False,
    )
    detector = vision.FaceLandmarker.create_from_options(options)
    try:
        result = detector.detect(image)
    finally:
        detector.close()
    if not result.face_landmarks:
        return None
    landmarks = result.face_landmarks[0]
    return [[float(p.x) * width, float(p.y) * height] for p in landmarks]


def analyze(image_path: Path, out_path: Path) -> Dict[str, Any]:
    landmarks = detect(image_path)
    if landmarks is None:
        raise SystemExit(
            "Лицо не найдено. Убедитесь, что портрет показывает лицо (в том числе "
            "частично) и модель MediaPipe установлена."
        )
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size
    payload = build_regions(landmarks, width, height)
    payload["source"] = image_path.name
    payload["crop_fraction"] = 0.70  # face height as a fraction of the canvas height
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    log.info("Записано %s", out_path)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Анализ портрета AI Companion")
    parser.add_argument("image", nargs="?", default=str(config.FRONTEND_DIR / "assets" / "avatar.png"))
    parser.add_argument("--out", default=str(config.FRONTEND_DIR / "assets" / "face.json"))
    parser.add_argument("--print", dest="print_only", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        log.error("Изображение не найдено: %s", image_path)
        return 2
    payload = analyze(image_path, Path(args.out))
    if args.print_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

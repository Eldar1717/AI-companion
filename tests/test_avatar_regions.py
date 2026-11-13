"""Portrait region analysis tests."""

from __future__ import annotations

import json
from pathlib import Path

from backend import config
from backend.avatar.analyze import build_regions


def synthetic_landmarks():
    """A simple synthetic face: two eyes and a mouth on a 600x800 canvas."""
    points = [[300.0, 400.0]] * 500
    eyes = {
        33: (200, 300), 133: (280, 300), 159: (240, 280), 145: (240, 320),
        263: (380, 300), 362: (300, 300), 386: (340, 280), 374: (340, 320),
        468: (230, 300), 473: (330, 300),
    }
    brows = {70: (210, 270), 63: (220, 272), 105: (250, 275), 66: (235, 273),
             107: (255, 274), 46: (200, 271), 53: (240, 276), 52: (230, 272),
             65: (245, 275), 55: (225, 273),
             300: (370, 270), 293: (360, 272), 334: (330, 275), 296: (345, 273),
             336: (325, 274), 276: (380, 271), 283: (350, 276), 282: (355, 272),
             295: (335, 275), 285: (365, 273)}
    mouth = {61: (250, 480), 185: (290, 470), 40: (270, 460), 39: (280, 465),
             37: (275, 455), 0: (285, 450), 267: (295, 452), 269: (300, 455),
             270: (305, 452), 409: (310, 455), 291: (330, 470),
             146: (250, 500), 91: (290, 505), 181: (285, 510), 84: (275, 508),
             17: (290, 515), 314: (305, 512), 405: (315, 510), 321: (320, 512),
             375: (330, 500),
             78: (270, 485), 191: (290, 478), 80: (275, 482), 81: (280, 480),
             82: (285, 478), 13: (290, 475), 312: (300, 478), 311: (305, 480),
             310: (310, 478), 415: (315, 480), 308: (325, 478),
             95: (275, 500), 88: (285, 505), 178: (290, 508), 87: (280, 506),
             14: (290, 510), 317: (305, 510), 402: (315, 508), 318: (320, 510),
             324: (325, 508)}
    for index, (x, y) in {**eyes, **brows, **mouth}.items():
        points[index] = [float(x), float(y)]
    # face oval
    for index in (10, 151, 108, 107, 103, 66, 109, 377, 400, 152, 204, 212, 425,
                  441, 263, 280, 387, 362, 251, 284, 117, 287, 356, 345, 175,
                  148, 176, 149, 150, 485, 397, 498, 483, 50, 519, 522, 523,
                  524, 249, 388, 1):
        if index < len(points):
            points[index] = [300.0, 400.0]
    return points


def test_build_regions_returns_boxes():
    landmarks = synthetic_landmarks()
    payload = build_regions(landmarks, 600, 800)
    assert payload["width"] == 600 and payload["height"] == 800
    for name in ("right_eye", "left_eye", "mouth"):
        box = payload["parts"][name]["box"]
        assert len(box) == 4
        assert box[0] < box[2] and box[1] < box[3]
        assert box[0] >= 0 and box[1] >= 0
        assert box[2] <= 600 and box[3] <= 800


def test_shipped_face_json_is_valid():
    path = config.FRONTEND_DIR / "assets" / "face.json"
    if not path.exists():
        return
    payload = json.loads(path.read_text("utf-8"))
    assert payload["width"] > 0 and payload["height"] > 0
    assert len(payload["face_box"]) == 4
    for name in ("right_eye", "left_eye", "mouth"):
        assert name in payload["parts"]
        assert len(payload["parts"][name]["box"]) == 4


def test_portrait_exists():
    portrait = Path(config.FRONTEND_DIR / "assets" / "avatar.png")
    assert portrait.exists() and portrait.stat().st_size > 100_000

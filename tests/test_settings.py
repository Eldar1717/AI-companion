"""Settings storage and validation tests."""

from __future__ import annotations

import pytest

from backend.settings import SettingsError, defaults, schema, validate


def test_defaults_cover_schema():
    values = defaults()
    for entry in schema():
        assert entry["key"] in values


def test_validate_accepts_known_keys():
    merged = validate({"voice.rate": 1.4, "avatar.emotions": False})
    assert merged["voice.rate"] == 1.4
    assert merged["avatar.emotions"] is False


def test_validate_rejects_unknown_key():
    with pytest.raises(SettingsError):
        validate({"voice.ratex": 1})


def test_validate_rejects_out_of_range():
    with pytest.raises(SettingsError):
        validate({"voice.rate": 9})
    with pytest.raises(SettingsError):
        validate({"voice.volume": "loud"})


def test_settings_api_roundtrip(client):
    response = client.put("/api/settings", json={"voice.rate": 1.25})
    assert response.status_code == 200
    assert response.get_json()["settings"]["voice.rate"] == 1.25
    fetched = client.get("/api/settings").get_json()
    assert fetched["settings"]["voice"]["rate"] == 1.25


def test_personality_endpoint(client):
    text = "Ты — тестовый персонаж. Отвечаешь коротко."
    response = client.put("/api/settings/personality", json={"personality": text})
    assert response.status_code == 200
    assert client.get("/api/settings/personality").get_json()["personality"] == text
    rejected = client.put("/api/settings/personality", json={"personality": "x" * 40000})
    assert rejected.status_code == 400
    # the previous value must survive the rejected write
    assert client.get("/api/settings/personality").get_json()["personality"] == text

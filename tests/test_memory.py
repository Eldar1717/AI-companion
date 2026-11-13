"""Memory layer tests."""

from __future__ import annotations

from backend.models import memory


def test_add_and_recall(clean_db):
    memory.add_message("Привет", "user", source="text")
    memory.add_message("Как дела?", "user", source="voice")
    memory.add_message("Отлично!", "assistant", source="text")
    rows = memory.recent_messages(10)
    assert [row["role"] for row in rows] == ["user", "user", "assistant"]
    context = memory.recall_context(4)
    assert "Привет" in context and "Отлично!" in context


def test_clear_and_delete(clean_db):
    first = memory.add_message("один", "user")
    memory.add_message("два", "user")
    assert memory.count_messages() == 2
    assert memory.delete_message(first) is True
    assert memory.count_messages() == 1
    assert memory.clear_history() == 1
    assert memory.count_messages() == 0


def test_fact_lifecycle(clean_db):
    fact_id = memory.add_fact("user", "Я работаю программистом")
    assert memory.count_facts() == 1
    assert memory.delete_fact(fact_id) is True
    assert memory.count_facts() == 0
    assert len(memory.list_facts(include_deleted=True)) == 1


def test_extract_facts_self_description():
    facts = memory.extract_facts("Я работаю программистом и люблю кофе")
    assert facts and "программистом" in facts[0]


def test_extract_facts_explicit_request():
    facts = memory.extract_facts("Запомни, что у меня allergic на кошачью шерсть")
    assert facts and "кошачью шерсть" in facts[0]


def test_extract_facts_ignores_chatter():
    assert memory.extract_facts("Привет, как дела?") == []


def test_memory_endpoints(client, clean_db):
    memory.add_message("Помни меня", "user")
    listed = client.get("/api/memory").get_json()
    assert listed["status"]["messages"] == 1
    message_id = listed["messages"][0]["id"]
    assert client.delete(f"/api/memory/messages/{message_id}").status_code == 200
    assert client.get("/api/memory").get_json()["status"]["messages"] == 0
    assert client.delete("/api/memory/history").status_code == 200


def test_save_switch(client):
    assert client.post("/api/memory/save", json={"save": False}).status_code == 200
    assert client.get("/api/memory").get_json()["status"]["save_enabled"] is False
    assert client.post("/api/memory/save", json={"save": True}).status_code == 200
    assert client.post("/api/memory/save", json={"save": "yes"}).status_code == 400

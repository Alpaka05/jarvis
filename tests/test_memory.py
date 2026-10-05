from core.agent import JarvisAgent
from core.llm.base import LLMResponse, ToolCall
from core.memory import MemoryStore
from tools.memory_tool import MemoryTool

from tests.test_agent import FakeProvider


def test_add_search_update_delete(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    f1 = store.add_fact("Der Nutzer heißt Colin.", "person")
    f2 = store.add_fact("Das Badlicht ist light.licht_bad", "device")
    assert store.count_facts() == 2

    dup = store.add_fact("der nutzer heißt colin.", "person")
    assert dup.get("duplicate") and store.count_facts() == 2

    hits = store.search_facts("Licht im Bad")
    assert hits and hits[0]["id"] == f2["id"]

    assert store.update_fact(f1["id"], "Der Nutzer heißt Colin Benecke.")
    assert "Benecke" in store.list_facts()[0]["content"]

    assert store.delete_fact(f2["id"])
    assert not store.delete_fact(999)
    assert store.count_facts() == 1

    prompt = store.facts_for_prompt()
    assert f"[#{f1['id']}|person]" in prompt


def test_conversation_log_and_search(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.log_message("s1", "user", "Wie wird das Wetter in Berlin?")
    store.log_message("s1", "assistant", "Sonnig bei 24 Grad.")
    store.log_message("s1", "user", "Spiel Musik von Queen")
    hits = store.search_conversations("Wetter Berlin")
    assert hits and "Berlin" in hits[0]["content"]
    assert len(store.search_conversations("", days=1)) == 3


def test_memory_tool_actions(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    tool = MemoryTool(store)
    res = tool.execute(action="remember", content="Lieblingsmusik ist Rock", category="preference")
    assert res.success and "Gemerkt" in res.output
    fid = res.data["id"]
    assert "Rock" in tool.execute(action="recall", query="Musik").output
    assert tool.execute(action="update", id=fid, content="Lieblingsmusik ist Metal").success
    assert "Metal" in tool.execute(action="list").output
    assert tool.execute(action="forget", id=fid).success
    assert "leer" in tool.execute(action="list").output
    assert not tool.execute(action="forget", id=fid).success


def test_agent_injects_facts_and_logs_conversation(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.add_fact("Der Nutzer heißt Colin.", "person")
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="memory", arguments={"action": "remember", "content": "Colin mag Kaffee", "category": "preference"})]),
            LLMResponse(text="Gemerkt, Colin."),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[MemoryTool(store)], memory=store)
    answer = agent.process_query("Ich mag Kaffee")

    assert answer == "Gemerkt, Colin."
    assert "Der Nutzer heißt Colin." in provider.calls[0]["system"]
    assert store.count_facts() == 2
    logged = store.search_conversations("Kaffee")
    roles = {r["role"] for r in logged}
    assert roles == {"user"} or roles == {"user", "assistant"}
    assert any(r["content"] == "Gemerkt, Colin." for r in store.search_conversations("Gemerkt"))


def test_facts_for_prompt_prefers_recent_facts(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    old = store.add_fact("Alter Fakt eins.")
    store.add_fact("Alter Fakt zwei.")
    store.add_fact("Neuer Fakt drei.")
    # Uhrzeiten festlegen, damit die Reihenfolge nicht an der Sekunde hängt
    store._conn.execute("UPDATE facts SET updated_at = '2026-01-01T00:00:00'")
    store._conn.execute("UPDATE facts SET updated_at = '2026-02-01T00:00:00' WHERE content LIKE 'Neuer%'")
    store._conn.commit()

    prompt = store.facts_for_prompt(limit=2)
    assert "Neuer Fakt drei." in prompt
    assert prompt.count("\n") == 1

    store.update_fact(old["id"], "Korrigierter Fakt eins.")
    prompt = store.facts_for_prompt(limit=2)
    assert "Korrigierter Fakt eins." in prompt
    # Ausgabe in id-Reihenfolge (stabiler Prompt)
    assert prompt.index("Korrigierter") < prompt.index("Neuer")

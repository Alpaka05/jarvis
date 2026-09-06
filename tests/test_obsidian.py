from datetime import date

from tools.obsidian_tool import ObsidianTool


def make_vault(tmp_path):
    (tmp_path / "HomeLab.md").write_text("# HomeLab\n\nProxmox läuft auf dem Mini-PC.\nImmich für Fotos.\n", encoding="utf-8")
    (tmp_path / "Ethik").mkdir()
    (tmp_path / "Ethik" / "Vorlesung 3.md").write_text("Kant und der kategorische Imperativ.\n", encoding="utf-8")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "workspace.md").write_text("proxmox intern", encoding="utf-8")
    (tmp_path / "Anhänge").mkdir()
    return ObsidianTool(tmp_path)


def test_unconfigured_vault_fails_cleanly():
    res = ObsidianTool("").execute(action="list")
    assert not res.success and "OBSIDIAN_VAULT" in res.output


def test_search_skips_hidden_dirs_and_returns_snippets(tmp_path):
    tool = make_vault(tmp_path)
    res = tool.execute(action="search", query="proxmox")
    assert res.success and "HomeLab" in res.output and ".obsidian" not in res.output
    assert res.data[0]["snippets"] and "Proxmox" in res.data[0]["snippets"][0]

    res = tool.execute(action="search", query="gibtesnicht")
    assert res.success and res.data == []


def test_read_by_name_and_by_path(tmp_path):
    tool = make_vault(tmp_path)
    assert "Immich" in tool.execute(action="read", note="HomeLab").output
    assert "Kant" in tool.execute(action="read", note="Vorlesung 3").output  # nur Name, wie [[Link]]
    assert "Kant" in tool.execute(action="read", note="Ethik/Vorlesung 3.md").output
    assert not tool.execute(action="read", note="Nirgends").success


def test_list_folder_and_recent(tmp_path):
    tool = make_vault(tmp_path)
    res = tool.execute(action="list")
    assert res.success and "Ethik/" in res.output and "HomeLab" in res.output
    assert ".obsidian" not in res.output and "Anhänge/" in res.output

    res = tool.execute(action="list", folder="Ethik")
    assert res.data["notes"] == ["Vorlesung 3"]

    res = tool.execute(action="list", recent=1)
    assert res.success and len(res.data) == 1


def test_create_with_tags_and_no_overwrite(tmp_path):
    tool = make_vault(tmp_path)
    res = tool.execute(action="create", note="Fahrrad", folder="Projekte", content="Rahmen bestellen", tags=["#todo", "rad"])
    assert res.success and (tmp_path / "Projekte" / "Fahrrad.md").exists()
    text = (tmp_path / "Projekte" / "Fahrrad.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntags:\n  - todo\n  - rad\n---") and "Rahmen bestellen" in text

    res = tool.execute(action="create", note="HomeLab", content="x")
    assert not res.success and "existiert bereits" in res.output
    assert "Immich" in (tmp_path / "HomeLab.md").read_text(encoding="utf-8")


def test_append_and_path_traversal_blocked(tmp_path):
    tool = make_vault(tmp_path)
    res = tool.execute(action="append", note="HomeLab", content="Pi-hole einrichten")
    assert res.success
    assert (tmp_path / "HomeLab.md").read_text(encoding="utf-8").endswith("Immich für Fotos.\n\nPi-hole einrichten\n")

    assert not tool.execute(action="read", note="../../etc/passwd").success
    assert not tool.execute(action="create", note="../boese", content="x").success
    assert not (tmp_path.parent / "boese.md").exists()


def test_daily_creates_then_appends(tmp_path):
    tool = make_vault(tmp_path)
    today = date.today().isoformat()
    res = tool.execute(action="daily")
    assert res.success and (tmp_path / f"{today}.md").exists()
    res = tool.execute(action="daily", content="- Jarvis getestet")
    assert res.success and "ergänzt" in res.output
    assert "- Jarvis getestet" in (tmp_path / f"{today}.md").read_text(encoding="utf-8")


def test_confirmation_only_for_writes(tmp_path):
    tool = make_vault(tmp_path)
    assert tool.confirmation_prompt(action="search", query="x") is None
    assert tool.confirmation_prompt(action="daily") is None
    assert "anlegen" in tool.confirmation_prompt(action="create", note="Neu", content="Hallo")
    assert "anhängen" in tool.confirmation_prompt(action="append", note="HomeLab", content="Hallo")

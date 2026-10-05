import core.platform_utils as pu


def test_notify_mac_passes_text_as_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(pu, "IS_MAC", True)
    monkeypatch.setattr(pu.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    title = '" & (do shell script "touch /tmp/x") --'
    message = "x\\"
    assert pu.notify(title, message)

    cmd = calls[0]
    script = " ".join(cmd[: cmd.index("--")])
    # Nutzertext steht nur hinter "--" als Argument, nie im Skript selbst
    assert "touch" not in script and "x\\" not in script
    assert cmd[cmd.index("--") + 1 :] == [title, message]


def test_say_does_not_parse_text_as_options(monkeypatch):
    calls = []
    monkeypatch.setattr(pu, "IS_MAC", True)
    monkeypatch.setattr(pu, "IS_WINDOWS", False)
    monkeypatch.setattr(pu.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))

    pu.speak_system_process("-v? Liste", voice="Anna")
    assert calls[0][-2:] == ["--", "-v? Liste"]

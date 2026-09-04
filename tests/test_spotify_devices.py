from tools.spotify_tool import SpotifyTool

DEVICES = [
    {"id": "tv", "name": "TCL TV", "type": "TV", "is_active": False},
    {"id": "mac", "name": "Colins MacBook", "type": "Computer", "is_active": False},
    {"id": "pc", "name": "DESKTOPXII", "type": "Computer", "is_active": False},
    {"id": "echo", "name": "Colins Echo", "type": "Speaker", "is_active": False},
]


def test_wanted_device_wins():
    assert SpotifyTool.choose_device(DEVICES, "echo")["id"] == "echo"
    assert SpotifyTool.choose_device(DEVICES, "tv")["id"] == "tv"
    assert SpotifyTool.choose_device(DEVICES, "macbook")["id"] == "mac"


def test_active_device_preferred_over_list_order():
    devices = [dict(d) for d in DEVICES]
    devices[2]["is_active"] = True
    assert SpotifyTool.choose_device(devices)["id"] == "pc"


def test_preferred_name_then_computer_not_first_entry():
    assert SpotifyTool.choose_device(DEVICES, preferred="desktopxii")["id"] == "pc"
    # ohne Treffer für Hostname: irgendein Computer statt des Fernsehers
    chosen = SpotifyTool.choose_device(DEVICES, preferred="gibt-es-nicht")
    assert chosen["type"] == "Computer"


def test_no_devices():
    assert SpotifyTool.choose_device([]) is None

from datetime import datetime, timedelta

from tools import default_tools
from tools.calendar_tool import CalendarTool
from tools.system_tool import SystemTool


def test_all_tools_have_valid_schemas():
    names = set()
    for tool in default_tools():
        schema = tool.to_schema()
        assert schema["name"] and schema["name"] not in names
        names.add(schema["name"])
        assert schema["description"]
        params = schema["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        for required in params.get("required", []):
            assert required in params["properties"]


def test_calendar_add_list_delete(tmp_path):
    cal = CalendarTool(calendar_file=tmp_path / "cal.json")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    res = cal.execute(action="add", title="Zahnarzt", date=tomorrow, time="14:00")
    assert res.success and "Zahnarzt" in res.output

    res = cal.execute(action="list", days=3)
    assert res.success and "Zahnarzt" in res.output and "14:00" in res.output

    res = cal.execute(action="list", days=1)  # nur heute
    assert "Keine Termine" in res.output

    res = cal.execute(action="delete", title="zahnarzt")
    assert res.success and "1 Termin" in res.output
    assert cal.execute(action="list", days=3).data == []


def test_calendar_rejects_bad_date(tmp_path):
    cal = CalendarTool(calendar_file=tmp_path / "cal.json")
    res = cal.execute(action="add", title="x", date="morgen", time="14:00")
    assert not res.success


def test_system_time():
    res = SystemTool().execute(action="time")
    assert res.success and "Uhr" in res.output

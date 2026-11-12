import json
from pathlib import Path


def parse_port(value):
    """Accept integer ports 1..maximum and whitespace-padded ASCII digits."""
    maximum = json.loads(Path(__file__).with_name("settings.json").read_text())["maximum"]
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Expected an integer or ASCII decimal string")
    if isinstance(value, str):
        value = value.strip()
        if not value.isascii() or not value.isdecimal():
            raise ValueError("Expected ASCII decimal digits")
    number = int(value)
    if not 1 <= number <= maximum:
        raise ValueError("Port outside permitted range")
    return number

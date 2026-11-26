"""Linux CPU 센서값을 PLC의 0.1 °C 단위 UINT 워드로 변환한다."""
from pathlib import Path


UNAVAILABLE = 0xffff


def _text(path):
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def _warmest(paths):
    values = []
    for path in paths:
        try:
            millidegrees = int(_text(path))
        except ValueError:
            continue
        if 0 <= millidegrees <= 200_000:
            values.append(millidegrees)
    return (max(values) + 50) // 100 if values else UNAVAILABLE


def read_cpu_temperature_word(sys_class=Path("/sys/class")):
    """CPU 패키지 센서를 우선하고, 여러 CPU가 있으면 가장 높은 온도를 사용한다."""
    sensors = []
    for device in (sys_class / "hwmon").glob("hwmon*"):
        driver = _text(device / "name")
        if driver == "coretemp":
            for label in device.glob("temp*_label"):
                if _text(label).startswith("Package id "):
                    sensors.append(label.with_name(label.name.replace("_label", "_input")))
        elif driver in ("k10temp", "zenpower"):
            for label in device.glob("temp*_label"):
                if _text(label) == "Tctl":
                    sensors.append(label.with_name(label.name.replace("_label", "_input")))
    value = _warmest(sensors)
    if value != UNAVAILABLE:
        return value
    zones = (sys_class / "thermal").glob("thermal_zone*")
    return _warmest(zone / "temp" for zone in zones
                    if _text(zone / "type") in ("x86_pkg_temp", "cpu-thermal", "cpu_thermal"))

"""패키지에 포함된 애플리케이션 스타일을 불러온다."""

from importlib.resources import files


def load_stylesheet() -> str:
    """소스 실행과 PyInstaller 빌드 모두에서 패키지 QSS를 읽는다."""
    return files(__package__).joinpath("app.qss").read_text(encoding="utf-8")

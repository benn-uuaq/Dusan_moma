from importlib.resources import files


def load_stylesheet() -> str:
    return files(__package__).joinpath("app.qss").read_text(encoding="utf-8")

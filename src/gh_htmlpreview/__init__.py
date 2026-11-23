import importlib.metadata
from pathlib import Path
import tomllib


def _version() -> str:
    # Source checkout, as run by the gh extension
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    return importlib.metadata.version("gh-htmlpreview")


__version__ = _version()

__all__ = ["__version__"]

import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import gh_htmlpreview


@pytest.mark.parametrize(
    "pyproject",
    ['[project]\nname = "other"\nversion = "9.9.9"\n', "[tool.other]\n"],
    ids=["other project", "no project table"],
)
def test_version_ignores_other_pyproject(tmp_path, pyproject):
    """A copy two levels below another project's root, as from pip install --target."""
    (tmp_path / "pyproject.toml").write_text(pyproject)
    shutil.copytree(Path(gh_htmlpreview.__file__).parent, tmp_path / "vendor" / "gh_htmlpreview")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import gh_htmlpreview; print(gh_htmlpreview.__version__)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path / "vendor")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == importlib.metadata.version("gh-htmlpreview")

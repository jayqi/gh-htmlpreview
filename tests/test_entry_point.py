import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

REPO_DIR = Path(__file__).resolve().parents[1]
SCRIPT = REPO_DIR / "gh-htmlpreview"
VERSION = tomllib.loads((REPO_DIR / "pyproject.toml").read_text())["project"]["version"]
PYTHON = os.path.realpath(sys.executable)
OLD_PYTHON = "#!/bin/sh\nexit 1\n"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash entry point")


@pytest.fixture
def bin_dir(tmp_path):
    """A directory to use as the only entry on PATH, with the commands the script needs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ["bash", "dirname", "readlink"]:
        path = shutil.which(command)
        assert path is not None
        (bin_dir / command).symlink_to(path)
    return bin_dir


def add_fake(bin_dir: Path, name: str, content: str) -> None:
    fake = bin_dir / name
    fake.write_text(content)
    fake.chmod(0o755)


def run(
    bin_dir: Path,
    *args: str,
    script: Path = SCRIPT,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    full_env["PATH"] = str(bin_dir)
    full_env.update(env or {})
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=cwd or bin_dir,
    )


@pytest.fixture(params=["python3", "python"])
def python_bin_dir(request, bin_dir):
    (bin_dir / request.param).symlink_to(PYTHON)
    return bin_dir


def test_version(python_bin_dir):
    result = run(python_bin_dir, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION


def test_help(python_bin_dir):
    result = run(python_bin_dir, "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: gh htmlpreview ")


def test_arguments_forwarded(python_bin_dir):
    result = run(python_bin_dir, "https://github.com/o/r/blob/main/a.html", "--port", "8 0")
    assert result.returncode == 2
    assert result.stderr.startswith("usage: gh htmlpreview ")
    assert "'8 0'" in result.stderr


def test_skips_old_python3(bin_dir):
    add_fake(bin_dir, "python3", OLD_PYTHON)
    (bin_dir / "python").symlink_to(PYTHON)
    result = run(bin_dir, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION


@pytest.mark.parametrize("names", [[], ["python"], ["python3", "python"]])
def test_no_suitable_python(bin_dir, names):
    for name in names:
        add_fake(bin_dir, name, OLD_PYTHON)
    result = run(bin_dir, "--version")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "gh htmlpreview: requires uv or Python 3.11 or newer on PATH.\n"


@pytest.fixture
def uv_bin_dir(bin_dir):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH")
    (bin_dir / "uv").symlink_to(uv)
    # Discoverable by uv but not by the script's own python3/python search
    (bin_dir / f"python3.{sys.version_info.minor}").symlink_to(PYTHON)
    return bin_dir


def test_uv(uv_bin_dir):
    result = run(uv_bin_dir, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION


@pytest.mark.parametrize("activated", [False, True], ids=["in working directory", "activated"])
def test_uv_skips_virtual_environment(uv_bin_dir, tmp_path, activated):
    project = tmp_path / "project"
    venv = project / ".venv"
    subprocess.run([PYTHON, "-m", "venv", "--without-pip", str(venv)], check=True)
    (site_packages,) = venv.glob("lib/python3.*/site-packages")
    (site_packages / "marker.pth").write_text(
        "import sys; sys.stderr.write('virtual environment used\\n')\n"
    )
    env = {"VIRTUAL_ENV": str(venv)} if activated else {}
    result = run(uv_bin_dir, "--version", cwd=tmp_path if activated else project, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION
    assert result.stderr == ""


def test_uv_run_when_no_python_found(bin_dir, tmp_path):
    log = tmp_path / "uv.log"
    add_fake(
        bin_dir,
        "uv",
        f"""#!/bin/sh
echo "$*" >> '{log}'
if [ "$1" = python ]; then
    exit 2
fi
while [ "$1" != -- ]; do shift; done
shift 2
exec '{PYTHON}' "$@"
""",
    )
    result = run(bin_dir, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION
    calls = log.read_text().splitlines()
    assert calls[0] == "python find --system >=3.11"
    assert calls[1].startswith("run --no-project --isolated --python >=3.11 -- python -I -c ")


def test_installed_repository_symlink(python_bin_dir, tmp_path):
    """gh extension install . symlinks the repository directory."""
    link = tmp_path / "extensions" / "gh-htmlpreview"
    link.parent.mkdir()
    link.symlink_to(REPO_DIR)
    result = run(python_bin_dir, "--version", script=link / "gh-htmlpreview")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION


def test_relative_script_symlink(python_bin_dir, tmp_path):
    link_dir = tmp_path / "links"
    link_dir.mkdir()
    link = link_dir / "gh-htmlpreview"
    link.symlink_to(os.path.relpath(SCRIPT, link_dir))
    result = run(python_bin_dir, "--version", script=link)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VERSION

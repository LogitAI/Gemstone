"""
The Gemstone version, written once: the `version` of `pyproject.toml`'s [project] table, which the
client's Gradle build reads too (owner, 2026-11-21). The project is not installed as a package (no
build system), so `importlib.metadata` cannot supply it; it is read from the file here.
`api/tests/test_client_compat.py` fails on a second copy anywhere.
"""
import pathlib
import tomllib

_PYPROJECT = pathlib.Path(__file__).resolve().parents[3] / "pyproject.toml"

VERSION: str = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]

"""
The Gemstone version, defined once. The project is not installed as a package (no build system),
so `importlib.metadata` cannot supply it; `pyproject.toml` must carry the same string
(`api/tests/test_client_compat.py` checks that).
"""
VERSION = "1.0.0"

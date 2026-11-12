import subprocess

import pytest

from secours_tech.tools import run_tool


def test_a_missing_tool_says_what_to_install(monkeypatch):
    monkeypatch.setattr("secours_tech.tools.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="install poppler"):
        run_tool("pdftotext", "-v")


def test_a_hung_tool_is_stopped_by_the_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        run_tool("sleep", "5", timeout=1)


def test_output_is_read_as_utf8_whatever_the_locale(monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    assert run_tool("printf", "Sécurité") == "Sécurité"

#!/usr/bin/env python3
"""Unit tests for .env loading and URL configuration in config.py."""

import os
from pathlib import Path
import sys

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan import config  # noqa: E402


def test_load_dotenv_parses_values_and_preserves_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        """
# Comment line
SCHOOL_CLASSROOM_URL="https://classroom.example.edu"
export SCHOOL_OUTLOOK_URL='https://mail.example.edu/inbox/'
SCHOOL_CDP_URL=http://127.0.0.1:9333
SCHOOL_ALREADY_SET=from-dotenv
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SCHOOL_CLASSROOM_URL", raising=False)
    monkeypatch.delenv("SCHOOL_OUTLOOK_URL", raising=False)
    monkeypatch.delenv("SCHOOL_CDP_URL", raising=False)
    monkeypatch.setenv("SCHOOL_ALREADY_SET", "from-shell")

    loaded = config.load_dotenv(env_file)
    assert loaded["SCHOOL_CLASSROOM_URL"] == "https://classroom.example.edu"
    assert os.environ["SCHOOL_CLASSROOM_URL"] == "https://classroom.example.edu"
    assert os.environ["SCHOOL_OUTLOOK_URL"] == "https://mail.example.edu/inbox/"
    assert os.environ["SCHOOL_CDP_URL"] == "http://127.0.0.1:9333"
    assert os.environ["SCHOOL_ALREADY_SET"] == "from-shell"


def test_config_exposes_url_settings():
    assert isinstance(config.CDP_URL, str) and config.CDP_URL
    assert isinstance(config.CLASSROOM_URL, str) and config.CLASSROOM_URL
    assert isinstance(config.CLASSROOM_HOST, str) and config.CLASSROOM_HOST
    assert isinstance(config.OUTLOOK_URL, str) and config.OUTLOOK_URL
    assert isinstance(config.OUTLOOK_HOSTS, list) and len(config.OUTLOOK_HOSTS) >= 1
    assert isinstance(config.CALENDAR_UID_DOMAIN, str) and config.CALENDAR_UID_DOMAIN

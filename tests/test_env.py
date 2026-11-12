from secours_tech.env import setting


def test_a_setting_comes_from_the_environment_then_from_dot_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('DATABASE_URL="postgresql:///from_file"\nOTHER=x\n', encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert setting("DATABASE_URL", env) == "postgresql:///from_file"
    monkeypatch.setenv("DATABASE_URL", "postgresql:///from_env")
    assert setting("DATABASE_URL", env) == "postgresql:///from_env"
    assert setting("MISSING", env) == ""
    assert setting("DATABASE_URL", tmp_path / "absent") == "postgresql:///from_env"

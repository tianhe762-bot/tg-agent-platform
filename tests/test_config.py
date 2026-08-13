from src.config import AppConfig


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_AGENT_ROOT", str(tmp_path))
    cfg = AppConfig.from_env(tmp_path / "nonexistent.env")
    assert cfg.root == tmp_path.resolve()
    assert cfg.bot_token == ""
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.degrade1_load == 2.8
    assert cfg.exec_calls_per_min == 20
    assert ["/usr/sbin/reboot"] in cfg.sudo_whitelist


def test_from_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_AGENT_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    env = tmp_path / "test.env"
    env.write_text(
        "\n".join(
            [
                "BOT_TOKEN=123:abc",
                "ADMIN_IDS=111, 222",
                "LLM_MODEL=my-model",
                "MIHOMO_API=http://127.0.0.1:9999",
                "EXEC_CALLS_PER_MIN=5",
                "WATCHDOG_ENABLED=true",
                "PANEL_SERVICES=下载器=8080 网盘=5244",
            ]
        ),
        encoding="utf-8",
    )
    cfg = AppConfig.from_env(env)
    assert cfg.bot_token == "123:abc"
    assert cfg.admin_ids == [111, 222]
    assert cfg.llm_model == "my-model"
    assert cfg.exec_calls_per_min == 5
    assert cfg.watchdog_enabled is True
    assert cfg.panel_services == ["下载器=8080", "网盘=5244"]


def test_hot_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_AGENT_ROOT", str(tmp_path))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    env = tmp_path / "test.env"
    env.write_text("LLM_MODEL=a\n", encoding="utf-8")
    cfg = AppConfig.from_env(env)
    assert cfg.llm_model == "a"
    assert not cfg.changed()

    import time

    time.sleep(0.01)
    env.write_text("LLM_MODEL=b\n", encoding="utf-8")
    assert cfg.changed()
    assert cfg.reload() is True
    assert cfg.llm_model == "b"


def test_ensure_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_AGENT_ROOT", str(tmp_path))
    cfg = AppConfig.from_env(tmp_path / "nonexistent.env")
    cfg.ensure_dirs()
    assert cfg.data_dir.exists()
    assert cfg.control_dir.exists()
    assert cfg.log_dir.exists()

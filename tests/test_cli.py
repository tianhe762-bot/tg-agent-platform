from src import cli


def test_cli_reload_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_AGENT_ROOT", str(tmp_path))
    assert cli.main(["reload"]) == 0
    flag = tmp_path / "data" / "control" / "reload"
    assert flag.exists()


def test_cli_soft_reset_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_AGENT_ROOT", str(tmp_path))
    assert cli.main(["soft-reset"]) == 0
    assert (tmp_path / "data" / "control" / "soft_reset").exists()

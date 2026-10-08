import pytest


@pytest.fixture(autouse=True)
def _fake_files_play(monkeypatch, request):
    """Tests use fake audio files (b'audio'): the real play test would reject them. Tests of the play
    test itself are marked 'real_play_test'."""
    if "real_play_test" not in request.keywords:
        from app.core import m4b
        monkeypatch.setattr(m4b, "play_test", lambda *a, **kw: "")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_play_test: use the real ffmpeg play test")

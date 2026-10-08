import hashlib
import io
import zipfile

import pytest

from app.core import tools


def _fake_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ffmpeg-8.0-essentials_build/bin/ffmpeg.exe", b"FFMPEG")
        z.writestr("ffmpeg-8.0-essentials_build/bin/ffprobe.exe", b"FFPROBE")
        z.writestr("ffmpeg-8.0-essentials_build/bin/ffplay.exe", b"not wanted")
        z.writestr("ffmpeg-8.0-essentials_build/LICENSE", b"GPL")
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, tmp_path, checksum):
    data = _fake_zip()
    monkeypatch.setattr(tools, "TOOLS_DIR", tmp_path / "BookSorter" / "ffmpeg")
    monkeypatch.setattr(tools.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp((checksum(data) + "  x.zip").encode() if req.full_url.endswith(".sha256") else data))


def test_download_keeps_only_the_tools(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, lambda d: hashlib.sha256(d).hexdigest())
    folder = tools.download_ffmpeg()
    assert sorted(p.name for p in folder.iterdir()) == ["LICENSE", "ffmpeg.exe", "ffprobe.exe"]
    assert (folder / "ffmpeg.exe").read_bytes() == b"FFMPEG"


def test_download_with_wrong_checksum_installs_nothing(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, lambda d: "0" * 64)
    with pytest.raises(OSError, match="checksum"):
        tools.download_ffmpeg()
    assert not (tools.TOOLS_DIR / "ffmpeg.exe").exists()

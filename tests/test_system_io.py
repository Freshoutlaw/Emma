"""Tests for SystemIO file read/write — aiofiles fallback, byte-exact writes."""

import asyncio
import os

import pytest

from capabilities.system_io import SystemIO


class _FakeGuardian:
    def __init__(self):
        self.calls = []

    def guard(self, action, detail=None):
        self.calls.append((action, detail or {}))
        return None


class _AsyncFile:
    def __init__(self, fh):
        self._fh = fh

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._fh.close()
        return False

    async def read(self, n=-1):
        return self._fh.read(n)

    async def write(self, data):
        self._fh.write(data)


class _FakeAsyncOpen:
    """Mirrors the aiofiles.open surface SystemIO uses."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, mode, encoding=None, newline=None):
        self.calls.append((str(path), mode, encoding, newline))
        return _AsyncFile(open(path, mode, encoding=encoding, newline=newline))


@pytest.fixture
def io():
    return SystemIO(_FakeGuardian())


def test_write_read_roundtrip_is_byte_exact(io, tmp_path):
    path = str(tmp_path / "f.txt")
    content = "line one\nline two\nno trailing newline"

    async def run():
        result = await io.write_file(path, content)
        back = await io.read_file(path)
        return result, back

    result, back = asyncio.run(run())
    assert back == content
    assert result["bytes"] == len(content)
    # Byte-exact on disk — must hold on Windows too (no CRLF translation).
    assert (tmp_path / "f.txt").read_bytes() == content.encode()


def test_sync_fallback_when_aiofiles_missing(io, tmp_path, monkeypatch):
    import capabilities.system_io as sio

    monkeypatch.setattr(sio, "async_open", None)
    path = str(tmp_path / "sync.txt")

    async def run():
        await io.write_file(path, "sync fallback")
        return await io.read_file(path)

    assert asyncio.run(run()) == "sync fallback"


def test_aiofiles_branch_used_when_available(io, tmp_path, monkeypatch):
    import capabilities.system_io as sio

    fake = _FakeAsyncOpen()
    monkeypatch.setattr(sio, "async_open", fake)
    path = str(tmp_path / "sub" / "aio.txt")
    content = "via aiofiles\nsecond line"

    async def run():
        result = await io.write_file(path, content)
        back = await io.read_file(path)
        return result, back

    result, back = asyncio.run(run())
    assert back == content
    assert result["bytes"] == len(content)
    assert len(fake.calls) == 2, "both write and read should use the aiofiles branch"
    # newline="" must be forwarded so aiofiles writes stay byte-exact too.
    write_call = [c for c in fake.calls if "w" in c[1]]
    assert write_call and write_call[0][3] == ""


def test_read_truncates_at_max_bytes(io, tmp_path):
    path = str(tmp_path / "big.txt")
    content = "x" * 5000

    async def run():
        await io.write_file(path, content)
        return await io.read_file(path, max_bytes=100)

    assert asyncio.run(run()) == "x" * 100


def test_read_missing_file_raises(io, tmp_path):
    async def run():
        await io.read_file(str(tmp_path / "missing.txt"))

    try:
        asyncio.run(run())
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised is True


def test_read_directory_raises(io, tmp_path):
    async def run():
        await io.read_file(str(tmp_path))

    try:
        asyncio.run(run())
        raised = False
    except IsADirectoryError:
        raised = True
    assert raised is True


def test_write_creates_parent_directories(io, tmp_path):
    path = str(tmp_path / "a" / "b" / "c.txt")

    async def run():
        await io.write_file(path, "nested")
        return await io.read_file(path)

    assert asyncio.run(run()) == "nested"
    assert os.path.exists(path)


def test_guardian_gates_read_and_write(io, tmp_path):
    path = str(tmp_path / "g.txt")

    async def run():
        await io.write_file(path, "x")
        await io.read_file(path)

    asyncio.run(run())
    assert [c[0] for c in io.guardian.calls] == ["file_write", "read_file"]

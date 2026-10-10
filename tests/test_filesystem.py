"""tests/filesystem.py: a test that needs raw-byte file names is skipped, with its reason, only
where the filesystem refuses them (macOS) — never silently, and never because of a guess."""

import errno
import os

import pytest

from tests.filesystem import require_raw_byte_names


def test_where_raw_byte_names_work_nothing_is_skipped_or_left_behind(tmp_path) -> None:
    try:
        require_raw_byte_names(tmp_path)
    except pytest.skip.Exception:
        pytest.skip("this machine refuses raw-byte names; the next tests cover that path")
    assert os.listdir(tmp_path) == []


def test_a_filesystem_refusing_non_utf8_names_skips_the_test(tmp_path, monkeypatch) -> None:
    def refuse(path, *args, **kwargs):  # what APFS answers for b"...\xff"
        raise OSError(errno.EILSEQ, "Illegal byte sequence", path)

    monkeypatch.setattr(os, "mkdir", refuse)
    with pytest.raises(pytest.skip.Exception, match="not UTF-8"):
        require_raw_byte_names(tmp_path)


def test_any_other_error_is_a_failure_not_a_skip(tmp_path, monkeypatch) -> None:
    def deny(path, *args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", path)

    monkeypatch.setattr(os, "mkdir", deny)
    with pytest.raises(PermissionError):
        require_raw_byte_names(tmp_path)

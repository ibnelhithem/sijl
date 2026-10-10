"""What the test machine's filesystem accepts, for tests that need more than every system offers."""

import errno
import os
from pathlib import Path

import pytest


def require_raw_byte_names(folder: Path) -> None:
    """Skip the calling test unless a file name may be any bytes.

    Linux filesystems accept any byte but `/` and NUL, so a folder named b"r\\xff" exists there
    and Sijl must handle it. macOS's APFS refuses a name that is not valid UTF-8 with EILSEQ
    ("Illegal byte sequence"), so no such folder can exist on a Mac and the test has nothing to
    check. The filesystem is asked, not the platform name: a Linux machine with a UTF-8-only
    mount skips too, and the skip reason is printed in pytest's summary (`-ra`).
    """
    probe = os.fsencode(folder) + b"/raw-byte-probe-\xff"
    try:
        os.mkdir(probe)
    except OSError as exc:
        if exc.errno == errno.EILSEQ:
            pytest.skip("this filesystem refuses file names that are not UTF-8 (macOS APFS)")
        raise
    os.rmdir(probe)

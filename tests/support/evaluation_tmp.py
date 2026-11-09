"""Explicit external scratch for retained-output/recovery tests, including CI."""
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture(name="tmp_path")
def external_tmp_path():
    # CI's legacy --basetemp is inside the repository; work evaluations forbid that.
    with TemporaryDirectory() as directory:
        yield Path(directory)

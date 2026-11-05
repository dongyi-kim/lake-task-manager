"""Launch the optional UI browser, reusing installed Windows browsers when needed."""
import os
from pathlib import Path

import pytest


def launch_chromium(driver):
    explicit = os.environ.get("LTM_BROWSER_EXECUTABLE")
    if explicit:
        return driver.chromium.launch(executable_path=explicit)
    try:
        return driver.chromium.launch()
    except Exception as error:
        for path in (Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                     Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")):
            if path.exists():
                return driver.chromium.launch(executable_path=str(path))
        if "Executable doesn't exist" in str(error):
            pytest.skip("Optional Chromium binary is not installed")
        raise

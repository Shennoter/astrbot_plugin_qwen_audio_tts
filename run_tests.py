# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Shennoter
"""Run offline tests from a checkout with any directory name."""

import importlib.util
import sys
import unittest
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    package_name = "astrbot_plugin_qwen_audio_tts"
    spec = importlib.util.spec_from_file_location(
        package_name, root / "__init__.py", submodule_search_locations=[str(root)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        [
            package_name + ".test_plugin",
            package_name + ".test_pronunciation",
            package_name + ".test_dashboard",
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

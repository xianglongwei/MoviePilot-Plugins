"""Expose all repository unittest suites through python -m unittest."""

import importlib.util
import sys
from pathlib import Path


def load_tests(loader, standard_tests, pattern):
    root = Path(__file__).parent
    # Versioned suites reuse filenames; unique module names prevent v2/v3 import collisions.
    for path in sorted(root.rglob(pattern or "test*.py")):
        name = "tests." + "_".join(path.relative_to(root).with_suffix("").parts)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        standard_tests.addTests(loader.loadTestsFromModule(module))
    return standard_tests

"""QGIS test-runner entry points."""

import importlib
import os
import sys
import unittest


def _discover(package):
    """Discovers tests using fully qualified module names."""
    module = importlib.import_module(package)
    start_dir = os.path.dirname(os.path.abspath(module.__file__))
    return unittest.defaultTestLoader.discover(
        start_dir, top_level_dir=os.path.dirname(start_dir)
    )


def _run_tests(test_suite, package_name):
    """Runs a test suite and exits if it is empty or unsuccessful."""
    # Import lazily because unittest discovers this module outside QGIS.
    from osgeo import gdal
    from qgis.core import Qgis

    count = test_suite.countTestCases()
    print("########")
    print(f"Discovered {count} tests in {package_name}")
    print(f"Python GDAL : {gdal.VersionInfo('VERSION_NUM')}")
    print(f"QGIS version : {Qgis.version()}")
    print("########")
    if count == 0:
        # qgis_testrunner.sh detects failure by grepping for "FAILED".
        print(f"FAILED: no tests were discovered in {package_name}")
        sys.exit(1)

    result = unittest.TextTestRunner(verbosity=3, stream=sys.stdout).run(test_suite)

    if not result.wasSuccessful():
        sys.exit(1)


def test_package(package="location_service"):  # noqa: PT028
    """Runs tests for the requested package."""
    _run_tests(_discover(package), package)


def test_environment():
    """Runs tests for TESTING_PACKAGE, defaulting to location_service."""
    package = os.environ.get("TESTING_PACKAGE", "location_service")
    _run_tests(_discover(package), package)


if __name__ == "__main__":
    test_package()

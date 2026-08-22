import configparser
import os
import re
import unittest


# Based on https://github.com/felt/qgis-plugin/blob/main/felt/test/test_init.py
class TestInit(unittest.TestCase):
    """Validates the plugin metadata expected by QGIS."""

    def test_required_metadata_fields(self):
        # Keep this list aligned with the QGIS plugin validator:
        # https://github.com/qgis/qgis-django/blob/master/qgis-app/plugins/validator.py

        required_metadata = [
            "name",
            "description",
            "version",
            "qgisMinimumVersion",
            "author",
            "email",
            "about",
            "tracker",
            "repository",
        ]

        file_path = os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata.txt")
        )
        metadata = []
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(file_path)
        message = f'Cannot find a section named "general" in {file_path}'
        assert parser.has_section("general"), message
        metadata.extend(parser.items("general"))
        for expectation in required_metadata:
            message = (
                f'Cannot find metadata "{expectation}" '
                f"in metadata source ({file_path})."
            )

            assert expectation in dict(metadata), message

    def test_version_matches_pyproject(self):
        # CI imports through a symlink; pyproject.toml is beside the real path.
        plugin_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        pyproject_path = os.path.join(os.path.dirname(plugin_dir), "pyproject.toml")
        if not os.path.exists(pyproject_path):
            self.skipTest("pyproject.toml is not present (installed from zip)")

        parser = configparser.ConfigParser()
        parser.read(os.path.join(plugin_dir, "metadata.txt"))
        metadata_version = parser.get("general", "version")

        # QGIS 3.34 uses Python 3.9, which does not provide tomllib.
        with open(pyproject_path, encoding="utf-8") as f:
            match = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.MULTILINE)
        assert match, f"No version field found in {pyproject_path}"
        assert match.group(1) == metadata_version, (
            f"pyproject.toml version {match.group(1)} does not match "
            f"metadata.txt version {metadata_version}"
        )


if __name__ == "__main__":
    unittest.main()

from typing import Optional

from qgis.PyQt.QtCore import QSettings


class LocalizationPreferences:
    """Stores the last language and political view shared by Maps and Places."""

    SETTINGS_GROUP = "/location-service"
    KEY_LANGUAGE = "last_language"
    KEY_POLITICAL_VIEW = "last_political_view"

    @staticmethod
    def _text(value: object) -> str:
        """Returns a stripped string for a value read from QSettings."""
        return str(value or "").strip()

    def load(self) -> tuple[str, str]:
        """Returns the saved language and political view API values."""
        settings = QSettings()
        settings.beginGroup(self.SETTINGS_GROUP)
        language = self._text(settings.value(self.KEY_LANGUAGE, ""))
        political_view = self._text(settings.value(self.KEY_POLITICAL_VIEW, ""))
        settings.endGroup()
        return language, political_view

    def save(
        self,
        language: Optional[str],
        political_view: Optional[str],
    ) -> None:
        """Saves the language and political view API values."""
        settings = QSettings()
        settings.beginGroup(self.SETTINGS_GROUP)
        settings.setValue(self.KEY_LANGUAGE, self._text(language))
        settings.setValue(self.KEY_POLITICAL_VIEW, self._text(political_view))
        settings.endGroup()
        settings.sync()

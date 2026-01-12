from typing import Any, ClassVar

from PyQt5.QtCore import QSettings


class ConfigurationHandler:
    """
    A singleton class for managing configuration settings for the
    Amazon Location Service Plugin.
    """

    _instance: ClassVar[None] = None
    _settings: ClassVar[dict[str, Any]] = {}
    SETTING_GROUP: ClassVar[str] = "/location-service"
    DEFAULT_SETTINGS: ClassVar[dict[str, str]] = {
        "region_value": "",
        "apikey_value": "",
    }

    def __new__(cls) -> "ConfigurationHandler":
        """
        Creates or retrieves a singleton instance of ConfigurationHandler.

        Returns:
            ConfigurationHandler: A singleton instance of the ConfigurationHandler.
        """
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """
        Initializes the ConfigurationHandler with default settings and
        loads existing settings.
        """
        if not hasattr(self, "_initialized"):
            self._settings = self.DEFAULT_SETTINGS.copy()
            self.initialize_settings()
            self._initialized = True

    def initialize_settings(self) -> None:
        """
        Initializes or updates settings from persistent storage using QSettings.
        """
        qsettings = QSettings()
        qsettings.beginGroup(self.SETTING_GROUP)
        for key, default in self.DEFAULT_SETTINGS.items():
            self._settings[key] = qsettings.value(key, default)
        qsettings.endGroup()

    def store_setting(self, key: str, value: Any) -> None:
        """
        Stores a single setting value in persistent storage.
        """
        qsettings = QSettings()
        qsettings.beginGroup(self.SETTING_GROUP)
        qsettings.setValue(key, value)
        qsettings.endGroup()
        self._settings[key] = qsettings.value(key, value)

    def get_setting(self, key: str) -> Any:
        """
        Retrieves a setting value from the internal dictionary.

        Args:
            key (str): The key of the setting to retrieve.

        Returns:
            str: The value of the setting, or an empty string if the key is not found.
        """
        return self._settings.get(key, None)

    def get_settings(self) -> dict[str, Any]:
        """
        Retrieves all settings as a dictionary.

        Returns:
            dict: A dictionary containing all settings.
        """
        return self._settings.copy()

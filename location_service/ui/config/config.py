import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...utils.configuration_handler import ConfigurationHandler


class ConfigUi(QDialog):
    """
    Provides a configuration dialog for the Amazon Location Service Plugin.
    """

    UI_PATH = os.path.join(os.path.dirname(__file__), "config.ui")
    KEY_REGION = "region_value"
    KEY_APIKEY = "apikey_value"

    def __init__(self) -> None:
        """
        Initializes the configuration dialog by loading the UI components,
        connecting signals and slots, and populating the form fields with
        existing configuration values.
        """
        super().__init__()
        self.ui = uic.loadUi(self.UI_PATH, self)
        self.button_save.clicked.connect(self._save)
        self.button_cancel.clicked.connect(self._cancel)
        self.configuration_handler = ConfigurationHandler()
        self._load_settings()

    def _load_settings(self) -> None:
        """
        Loads the settings from the configuration handler and populates the
        UI components with these values.

        Settings include:
        - region_value: AWS region
        - apikey_value: API key for authentication
        """
        for setting_key, line_edit in [
            (self.KEY_REGION, self.region_lineEdit),
            (self.KEY_APIKEY, self.apikey_lineEdit),
        ]:
            value = self.configuration_handler.get_setting(setting_key)
            line_edit.setText(value)

    def _save(self) -> None:
        """
        Saves the modified settings to the configuration manager and
        closes the dialog. This method captures all form values and
        persists them using the configuration handler.

        Raises:
            Exception: If an error occurs during the saving of settings.
        """
        settings = {
            self.KEY_REGION: self.region_lineEdit.text(),
            self.KEY_APIKEY: self.apikey_lineEdit.text(),
        }
        try:
            for key, value in settings.items():
                self.configuration_handler.store_setting(key, value)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e!r}")
        else:
            self.close()

    def _cancel(self) -> None:
        """
        Closes the configuration dialog without saving any changes.
        """
        self.close()

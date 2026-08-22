import os
from typing import Optional

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QLineEdit, QWidget

from ...utils.configuration_handler import (
    APIKEY_REMOVAL_INCOMPLETE_WARNING,
    REGION_FORMAT_HINT,
    ConfigurationHandler,
)
from ...utils.feedback import (
    APIKEY_PLAINTEXT_HINT,
    SUCCESS,
    WARNING,
    push_message,
    show_error,
)
from ..style_loader import load_style

try:
    _NORMAL_ECHO = QLineEdit.Normal
    _PASSWORD_ECHO = QLineEdit.Password
except AttributeError:
    _NORMAL_ECHO = QLineEdit.EchoMode.Normal
    _PASSWORD_ECHO = QLineEdit.EchoMode.Password


class ConfigUi(QDialog):
    """Amazon Location Service configuration dialog."""

    UI_PATH = os.path.join(os.path.dirname(__file__), "config.ui")
    KEY_REGION = ConfigurationHandler.KEY_REGION
    KEY_APIKEY = ConfigurationHandler.KEY_APIKEY

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Loads the dialog and connects its controls."""
        super().__init__(parent)
        uic.loadUi(self.UI_PATH, self)
        load_style(self)
        self.button_save.clicked.connect(self._save)
        self.button_cancel.clicked.connect(self._cancel)
        self.apikey_show_checkBox.toggled.connect(self._toggle_key_visibility)
        self.configuration_handler = ConfigurationHandler()
        self._apikey_readable = True

    def showEvent(self, event) -> None:
        """Masks the key when opened, but not after minimizing and restoring."""
        super().showEvent(event)
        if event.spontaneous():
            return
        self.apikey_show_checkBox.setChecked(False)
        self.apikey_lineEdit.setEchoMode(_PASSWORD_ECHO)

    def _toggle_key_visibility(self, show: bool) -> None:
        """Shows or masks the API key."""
        self.apikey_lineEdit.setEchoMode(_NORMAL_ECHO if show else _PASSWORD_ECHO)

    def reload_settings(self) -> None:
        """
        Loads stored settings before showing the dialog.

        Reading the encrypted API key may prompt for QGIS's master password,
        so this must not run during ``showEvent`` or plugin initialization. If
        the prompt is declined, ``_save`` must not treat the empty field as a
        deletion request.
        """
        handler = self.configuration_handler
        self._apikey_readable = handler.sync_apikey()
        self.region_lineEdit.setText(str(handler.get_setting(self.KEY_REGION) or ""))
        self.apikey_lineEdit.setText(str(handler.get_setting(self.KEY_APIKEY) or ""))
        self.apikey_lineEdit.setPlaceholderText(
            "" if self._apikey_readable else "Locked - enter the QGIS master password"
        )
        if warning := handler.apikey_sync_warning:
            push_message(WARNING, warning, duration=8)

    def _save(self) -> None:
        """Validates and saves the form, leaving it open when an error occurs."""
        region = self.region_lineEdit.text().strip()
        apikey = self.apikey_lineEdit.text().strip()
        self.region_lineEdit.setText(region)
        self.apikey_lineEdit.setText(apikey)
        if region and not ConfigurationHandler.REGION_PATTERN.match(region):
            show_error(
                self,
                "Input Error",
                f"Invalid region format: {region!r}. {REGION_FORMAT_HINT}",
            )
            self.region_lineEdit.setFocus()
            return
        # Empty means delete only when the stored key was readable.
        save_apikey = bool(apikey) or self._apikey_readable
        stored_as_plaintext = False
        apikey_saved = False
        try:
            if save_apikey:
                stored_as_plaintext = self.configuration_handler.save_apikey(apikey)
                apikey_saved = True
            self.configuration_handler.save_region(region)
        except Exception as e:
            message = f"Failed to save settings: {e}"
            if apikey_saved:
                if not apikey:
                    message += " The API key was cleared"
                elif stored_as_plaintext:
                    message += " The API key was saved as plain text"
                else:
                    message += " The API key was saved"
                message += ", but the region was not saved."
            else:
                message += (
                    " Some changes may already have been applied; reopen Config "
                    "to verify the stored values."
                )
            show_error(self, "Error", message)
            return
        self._report_save_result(apikey, save_apikey, stored_as_plaintext)
        self.close()

    def _report_save_result(
        self, apikey: str, save_apikey: bool, stored_as_plaintext: bool
    ) -> None:
        """Reports successful saves and any nonfatal API-key warning."""
        warning = self.configuration_handler.apikey_sync_warning
        if warning == APIKEY_REMOVAL_INCOMPLETE_WARNING:
            # Do not imply that a deletion succeeded when encrypted storage was
            # unavailable and may still contain the key.
            push_message(WARNING, warning, duration=8)
            return
        push_message(SUCCESS, "Settings saved.")
        if not save_apikey:
            push_message(
                WARNING,
                "The API key was left unchanged: the authentication database "
                "is locked.",
                duration=8,
            )
        elif warning and warning != APIKEY_REMOVAL_INCOMPLETE_WARNING:
            push_message(WARNING, warning, duration=8)
        elif apikey and stored_as_plaintext:
            push_message(WARNING, APIKEY_PLAINTEXT_HINT, duration=8)

    def _cancel(self) -> None:
        """Closes the dialog without saving."""
        self.close()

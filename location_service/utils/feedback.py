from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from qgis.core import Qgis
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget
from qgis.utils import iface

from .configuration_handler import AuthDatabaseLockedError
from .redaction import redact_secrets

PLUGIN_NAME = "Amazon Location Service"
CONFIG_MISSING_HINT = "Open the Config menu and set your AWS region and API key first."
APIKEY_PLAINTEXT_HINT = (
    "The API key could not be stored in the QGIS authentication database, "
    "so it was saved as plain text in your QGIS settings."
)

_WAIT_CURSOR = Qt.CursorShape.WaitCursor
_PLAIN_TEXT = Qt.TextFormat.PlainText
_ICON_WARNING = QMessageBox.Icon.Warning
_ICON_CRITICAL = QMessageBox.Icon.Critical

INFO = Qgis.MessageLevel.Info
SUCCESS = Qgis.MessageLevel.Success
WARNING = Qgis.MessageLevel.Warning


@contextlib.contextmanager
def busy_operation(button: QPushButton, busy_text: str) -> Iterator[None]:
    """Shows a wait cursor and disables the button to prevent duplicate actions."""
    QApplication.setOverrideCursor(_WAIT_CURSOR)
    original_text = button.text()
    original_enabled = button.isEnabled()
    button.setText(busy_text)
    button.setEnabled(False)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()
        button.setText(original_text)
        button.setEnabled(original_enabled)


def push_message(level: Any, text: str, duration: int = 5) -> None:
    """Posts a redacted, non-blocking notification to the QGIS message bar."""
    if iface is None:
        return
    message_bar = iface.messageBar()
    if message_bar is not None:
        message_bar.pushMessage(
            PLUGIN_NAME, redact_secrets(text), level=level, duration=duration
        )


def _show_message_box(parent: QWidget, icon: Any, title: str, text: str) -> None:
    """
    Shows a redacted modal message as plain text.

    Plain-text mode prevents server-supplied error text from being interpreted
    as HTML.
    """
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(_PLAIN_TEXT)
    box.setText(redact_secrets(text))
    box.exec()


def show_warning(parent: QWidget, title: str, text: str) -> None:
    """Shows a modal warning box with plain-text rendering."""
    _show_message_box(parent, _ICON_WARNING, title, text)


def show_error(parent: QWidget, title: str, text: str) -> None:
    """Shows a modal error box with plain-text rendering."""
    _show_message_box(parent, _ICON_CRITICAL, title, text)


def show_config_error(parent: QWidget, error: Exception) -> None:
    """Shows a configuration error with the appropriate recovery hint."""
    if isinstance(error, AuthDatabaseLockedError):
        show_warning(parent, "Configuration Required", str(error))
    else:
        show_warning(
            parent, "Configuration Required", f"{error}\n\n{CONFIG_MISSING_HINT}"
        )

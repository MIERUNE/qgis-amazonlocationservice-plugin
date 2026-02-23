import os

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import QWidget

STYLE_PATH = os.path.join(os.path.dirname(__file__), "style.qss")


def load_style(widget: QWidget) -> None:
    """
    Loads a QSS stylesheet and applies it to the specified widget.

    Args:
        widget: The target widget to apply the stylesheet to.
    """
    try:
        with open(STYLE_PATH, encoding="utf-8") as f:
            widget.setStyleSheet(f.read())
    except (OSError, UnicodeDecodeError) as e:
        QgsMessageLog.logMessage(
            f"Failed to load style: {e!r}",
            "Amazon Location Service",
            Qgis.Warning,
        )

import os

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtWidgets import QWidget

_UI_DIR = os.path.dirname(__file__)
STYLE_PATH = os.path.join(_UI_DIR, "style.qss")
CHECKMARK_PATH = os.path.join(_UI_DIR, "checkmark.svg").replace("\\", "/")


def load_style(widget: QWidget) -> None:
    """
    Loads a QSS stylesheet and applies it to the specified widget.

    Args:
        widget: The target widget to apply the stylesheet to.
    """
    try:
        with open(STYLE_PATH, encoding="utf-8") as f:
            qss = f.read().replace("${CHECKMARK_PATH}", CHECKMARK_PATH)
            widget.setStyleSheet(qss)
    except (OSError, UnicodeDecodeError) as e:
        QgsMessageLog.logMessage(
            f"Failed to load style: {e!r}",
            "Amazon Location Service",
            Qgis.Warning,
        )

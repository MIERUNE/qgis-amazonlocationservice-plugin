from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices


class TermsUi:
    """Provides access to the AWS Service Terms page."""

    SERVICE_TERMS_URL = "https://aws.amazon.com/service-terms"

    def open_service_terms_url(self) -> bool:
        """Opens the page in the default browser and reports whether it succeeded."""
        return QDesktopServices.openUrl(QUrl(self.SERVICE_TERMS_URL))

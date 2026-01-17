from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices


class TermsUi:
    """
    Opens the AWS service terms URL in the default web browser.
    """

    SERVICE_TERMS_URL = "https://aws.amazon.com/service-terms"

    def open_service_terms_url(self) -> None:
        """
        Opens the AWS service terms URL in the default web browser.

        Raises:
            ValueError: If the URL is malformed.
            OSError: If the browser fails to open.
        """
        url_object = QUrl(self.SERVICE_TERMS_URL)
        if not url_object.isValid():
            raise ValueError(f"Invalid URL: {self.SERVICE_TERMS_URL}")
        if not QDesktopServices.openUrl(url_object):
            raise OSError("Failed to open the web browser.")

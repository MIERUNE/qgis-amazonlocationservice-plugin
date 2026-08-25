from __future__ import annotations

import re
from typing import Any, ClassVar

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSettings

_NO_SETTINGS_ERROR = QSettings.Status.NoError

REGION_FORMAT_HINT = "Expected an AWS region code such as 'ap-northeast-1'."
APIKEY_LOCKED_HINT = (
    "The API key is stored in the QGIS authentication database, which is "
    "locked. Enter the QGIS master password and try again."
)
APIKEY_CLEANUP_WARNING = (
    "The API key is available, but its plaintext copy could not be removed "
    "from QGIS settings. Restart QGIS to retry."
)
APIKEY_REMOVAL_INCOMPLETE_WARNING = (
    "The API key was removed from QGIS settings, but an encrypted copy could "
    "not be checked or removed because QGIS authentication is unavailable. "
    "Enable QGIS authentication and delete the API key again."
)


class ConfigurationError(ValueError):
    """Raised for invalid configuration or persistence failures."""


class AuthDatabaseLockedError(ConfigurationError):
    """Raised when a stored API key cannot be read from QGIS auth storage."""


class ConfigurationHandler:
    """
    Manages the plugin's region and API key settings.

    The region (not a secret) lives in QSettings. The API key is stored
    encrypted in the QGIS authentication database when it is available and
    falls back to QSettings otherwise (headless runs, auth system disabled,
    or the user declining the master password).
    """

    _instance: ClassVar[ConfigurationHandler | None] = None
    SETTING_GROUP: ClassVar[str] = "/location-service"
    KEY_REGION = "region_value"
    KEY_APIKEY = "apikey_value"  # pragma: allowlist secret
    AUTH_APIKEY_NAME = "location-service-apikey"  # pragma: allowlist secret
    DEFAULT_SETTINGS: ClassVar[dict[str, str]] = {
        KEY_REGION: "",
        KEY_APIKEY: "",
    }
    # Region is interpolated into a hostname; use a conservative shape to prevent
    # host redirection. Unlike $, \Z also rejects a trailing newline.
    REGION_PATTERN = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]+\Z")

    def __new__(cls) -> ConfigurationHandler:
        """Returns the process-wide handler instance."""
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Loads the initial settings once."""
        if not hasattr(self, "_initialized"):
            self._settings: dict[str, Any] = self.DEFAULT_SETTINGS.copy()
            self._apikey_synced = False
            self.initialize_settings()
            self._initialized = True

    def initialize_settings(self) -> None:
        """
        Reloads QSettings without accessing encrypted auth storage.

        Encrypted auth is loaded lazily by ``sync_apikey`` because reading it
        may prompt for the QGIS master password.
        """
        qsettings = QSettings()
        qsettings.beginGroup(self.SETTING_GROUP)
        for key, default in self.DEFAULT_SETTINGS.items():
            self._settings[key] = qsettings.value(key, default)
        qsettings.endGroup()
        self._apikey_synced = False
        self._apikey_sync_warning: str | None = None

    @staticmethod
    def _auth_manager() -> Any:
        """Returns the enabled QGIS auth manager, or ``None``."""
        # authManager() may hang or crash when no QgsApplication is running.
        if QgsApplication.instance() is None:
            return None
        manager = QgsApplication.authManager()
        if manager is None or manager.isDisabled():
            return None
        return manager

    def sync_apikey(self) -> bool:
        """
        Synchronizes the cached API key with QGIS auth storage on demand.

        Auth access may prompt for the master password, so callers must be
        user-triggered. Returns ``False`` when an existing auth entry cannot be
        read; that outcome remains retryable. Cleanup failures leave the key
        usable and set ``apikey_sync_warning``.
        """
        if self._apikey_synced:
            return True
        manager = self._auth_manager()
        if manager is None:
            # Leave this unsynced so auth storage is retried if it becomes available.
            return True
        plaintext = self._settings.get(self.KEY_APIKEY) or ""
        if plaintext:
            # Keep the plaintext fallback usable and avoid retrying this session.
            if manager.storeAuthSetting(self.AUTH_APIKEY_NAME, plaintext, True):
                try:
                    self._remove_qsettings_value(self.KEY_APIKEY)
                except ConfigurationError:
                    # Keep this synced; settings reload retries the failed cleanup.
                    self._apikey_sync_warning = APIKEY_CLEANUP_WARNING
            self._apikey_synced = True
            return True
        if not manager.existsAuthSetting(self.AUTH_APIKEY_NAME):
            # Check before reading to avoid an unnecessary password prompt.
            self._apikey_synced = True
            return True
        stored = manager.authSetting(self.AUTH_APIKEY_NAME, "", True)
        if stored:
            self._settings[self.KEY_APIKEY] = stored
            self._apikey_synced = True
            return True
        # Empty auth values are never stored, so a falsy read means it is unreadable.
        return False

    def _remove_qsettings_value(self, key: str) -> None:
        """Deletes a value from the plugin's QSettings group."""
        qsettings = QSettings()
        qsettings.beginGroup(self.SETTING_GROUP)
        qsettings.remove(key)
        qsettings.endGroup()
        qsettings.sync()
        if qsettings.status() != _NO_SETTINGS_ERROR:
            raise ConfigurationError("Failed to remove a value from QGIS settings.")

    def _qsettings_contains(self, key: str) -> bool:
        """Returns whether the plugin's QSettings group contains a value."""
        qsettings = QSettings()
        qsettings.sync()
        if qsettings.status() != _NO_SETTINGS_ERROR:
            raise ConfigurationError("Failed to read a value from QGIS settings.")
        qsettings.beginGroup(self.SETTING_GROUP)
        contains = qsettings.contains(key)
        qsettings.endGroup()
        if qsettings.status() != _NO_SETTINGS_ERROR:
            raise ConfigurationError("Failed to read a value from QGIS settings.")
        return contains

    def _write_qsettings_value(self, key: str, value: str) -> None:
        """Writes a value and checks the QSettings operation status."""
        qsettings = QSettings()
        qsettings.beginGroup(self.SETTING_GROUP)
        qsettings.setValue(key, value)
        qsettings.endGroup()
        qsettings.sync()
        if qsettings.status() != _NO_SETTINGS_ERROR:
            raise ConfigurationError("Failed to write a value to QGIS settings.")

    def save_region(self, value: str) -> None:
        """Saves the region in QSettings."""
        self._write_qsettings_value(self.KEY_REGION, value)
        self._settings[self.KEY_REGION] = value

    def save_apikey(self, value: str) -> bool:
        """
        Saves or clears the API key.

        Nonempty keys use QGIS auth storage when possible and QSettings
        otherwise. When auth is available, clearing removes it before QSettings.

        Returns:
            ``True`` if a plaintext QSettings copy remains; otherwise ``False``.

        Raises:
            ConfigurationError: If a required persistence operation fails.
        """
        manager = self._auth_manager()
        if not value:
            if (
                manager is not None
                and manager.existsAuthSetting(self.AUTH_APIKEY_NAME)
                and not manager.removeAuthSetting(self.AUTH_APIKEY_NAME)
            ):
                raise ConfigurationError(
                    "Failed to remove the API key from the QGIS "
                    "authentication database."
                )
            # Remove auth first so failure does not discard a newer plaintext key.
            # Without auth access, an encrypted copy may reappear in a later session.
            self._remove_qsettings_value(self.KEY_APIKEY)
            self._settings[self.KEY_APIKEY] = ""
            # If auth is unavailable, keep synchronization retryable so a later
            # session can expose and remove any encrypted copy that remains.
            self._apikey_synced = manager is not None
            self._apikey_sync_warning = (
                APIKEY_REMOVAL_INCOMPLETE_WARNING if manager is None else None
            )
            return False

        plaintext_updated = False
        if manager is not None:
            # Update an existing fallback so stale plaintext cannot overwrite auth.
            if self._qsettings_contains(self.KEY_APIKEY):
                self._write_qsettings_value(self.KEY_APIKEY, value)
                plaintext_updated = True
            if manager.storeAuthSetting(self.AUTH_APIKEY_NAME, value, True):
                self._settings[self.KEY_APIKEY] = value
                self._apikey_synced = True
                if plaintext_updated:
                    try:
                        self._remove_qsettings_value(self.KEY_APIKEY)
                    except ConfigurationError:
                        self._apikey_sync_warning = APIKEY_CLEANUP_WARNING
                        return True
                self._apikey_sync_warning = None
                return False

        if not plaintext_updated:
            self._write_qsettings_value(self.KEY_APIKEY, value)
        self._settings[self.KEY_APIKEY] = value
        # A failed auth write is final for this session; if auth is unavailable,
        # leave synchronization retryable for a later call.
        self._apikey_synced = manager is not None
        self._apikey_sync_warning = None
        return True

    @property
    def apikey_sync_warning(self) -> str | None:
        """Returns the current API-key synchronization warning, if any."""
        return self._apikey_sync_warning

    def get_setting(self, key: str) -> Any:
        """Returns a cached setting value, or ``None`` if it is absent."""
        return self._settings.get(key, None)

    def get_settings(self) -> dict[str, Any]:
        """Returns a copy of the in-memory settings cache."""
        return self._settings.copy()

    def get_credentials(self) -> tuple[str, str]:
        """
        Returns the validated ``(region, API key)`` pair.

        An unreadable encrypted key raises ``AuthDatabaseLockedError``; other
        validation or persistence failures raise ``ConfigurationError``.
        """
        region = str(self.get_setting(self.KEY_REGION) or "").strip()
        if not region:
            raise ConfigurationError("Missing required configuration setting: region")
        if not self.REGION_PATTERN.match(region):
            raise ConfigurationError(
                f"Invalid region format: {region!r}. {REGION_FORMAT_HINT}"
            )
        # Validate before any operation that may prompt for a master password.
        if not self.sync_apikey():
            raise AuthDatabaseLockedError(APIKEY_LOCKED_HINT)
        apikey = str(self.get_setting(self.KEY_APIKEY) or "").strip()
        if not apikey:
            raise ConfigurationError("Missing required configuration setting: apikey")
        return region, apikey

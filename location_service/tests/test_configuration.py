import unittest
from unittest.mock import Mock, call, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.PyQt.QtCore import QSettings
    from qgis.PyQt.QtWidgets import QApplication

    from location_service.ui.config import config as config_module
    from location_service.ui.config.config import ConfigUi
    from location_service.utils.configuration_handler import (
        APIKEY_CLEANUP_WARNING,
        APIKEY_REMOVAL_INCOMPLETE_WARNING,
        AuthDatabaseLockedError,
        ConfigurationError,
        ConfigurationHandler,
    )

    APIKEY = ConfigurationHandler.KEY_APIKEY
    AUTH_NAME = ConfigurationHandler.AUTH_APIKEY_NAME
    SETTINGS_ACCESS_ERROR = QSettings.Status.AccessError
    SETTINGS_NO_ERROR = QSettings.Status.NoError


class FakeAuthManager:
    """Provides an in-memory stand-in for QgsAuthManager."""

    def __init__(self, locked=False):
        self.locked = locked
        self.entries = {}
        self.store_calls = 0

    def isDisabled(self):
        return False

    def existsAuthSetting(self, name):
        return name in self.entries

    def storeAuthSetting(self, name, value, encrypt=True):
        self.store_calls += 1
        if self.locked:
            return False
        self.entries[name] = value
        return True

    def authSetting(self, name, default="", decrypt=True):
        if self.locked:
            return default
        return self.entries.get(name, default)

    def removeAuthSetting(self, name):
        # Like QgsAuthManager, removing a missing entry reports failure.
        return self.entries.pop(name, None) is not None

    def masterPasswordIsSet(self):
        return not self.locked


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestApikeyLifecycle(unittest.TestCase):
    """Validates API-key synchronization between QSettings and QgsAuthManager."""

    def setUp(self):
        self.handler = ConfigurationHandler()
        self._original_settings = self.handler.get_settings()
        self._original_synced = self.handler._apikey_synced
        self._original_warning = self.handler.apikey_sync_warning
        self._original_manager = ConfigurationHandler.__dict__["_auth_manager"]
        self._original_qsettings = self._read_qsettings()
        self.manager = FakeAuthManager()
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)

    def tearDown(self):
        self.handler._settings = self._original_settings
        self.handler._apikey_synced = self._original_synced
        self.handler._apikey_sync_warning = self._original_warning
        ConfigurationHandler._auth_manager = self._original_manager
        self._write_qsettings(self._original_qsettings)

    @staticmethod
    def _read_qsettings():
        qsettings = QSettings()
        qsettings.beginGroup(ConfigurationHandler.SETTING_GROUP)
        values = {key: qsettings.value(key) for key in qsettings.childKeys()}
        qsettings.endGroup()
        return values

    @staticmethod
    def _write_qsettings(values):
        qsettings = QSettings()
        qsettings.beginGroup(ConfigurationHandler.SETTING_GROUP)
        qsettings.remove("")
        for key, value in values.items():
            qsettings.setValue(key, value)
        qsettings.endGroup()

    def _set_state(self, apikey, region="ap-northeast-1"):
        self.handler._settings = {
            ConfigurationHandler.KEY_REGION: region,
            APIKEY: apikey,
        }
        self.handler._apikey_synced = False
        self.handler._apikey_sync_warning = None

    def test_reads_encrypted_key(self):
        self.manager.entries[AUTH_NAME] = "encrypted-key"
        self._set_state("")
        assert self.handler.sync_apikey() is True
        assert self.handler.get_setting(APIKEY) == "encrypted-key"

    def test_no_password_prompt_when_nothing_is_stored(self):
        self.manager.locked = True
        self._set_state("")
        # A missing entry must not trigger decryption on a locked database.
        assert self.handler.sync_apikey() is True

    def test_locked_key_is_reported_and_retried(self):
        self.manager.entries[AUTH_NAME] = "encrypted-key"
        self.manager.locked = True
        self._set_state("")
        assert self.handler.sync_apikey() is False
        assert self.handler.sync_apikey() is False
        with self.assertRaises(AuthDatabaseLockedError):
            self.handler.get_credentials()
        self.manager.locked = False
        assert self.handler.sync_apikey() is True
        assert self.handler.get_setting(APIKEY) == "encrypted-key"

    def test_empty_auth_value_is_unreadable_even_when_auth_reports_unlocked(self):
        self.manager.entries[AUTH_NAME] = ""
        self._set_state("")

        assert self.manager.masterPasswordIsSet() is True
        assert self.handler.sync_apikey() is False
        assert self.handler.sync_apikey() is False
        assert self.handler._apikey_synced is False

    def test_migrates_plaintext_key(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        assert self.handler.save_apikey("plain-key") is True
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)

        assert self.handler.sync_apikey() is True
        assert self.manager.entries[AUTH_NAME] == "plain-key"
        assert APIKEY not in self._read_qsettings()

    def test_migration_cleanup_failure_keeps_key_usable_until_next_start(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("plain-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        qsettings = Mock()
        qsettings.status.return_value = SETTINGS_ACCESS_ERROR

        with patch(
            "location_service.utils.configuration_handler.QSettings",
            return_value=qsettings,
        ):
            assert self.handler.sync_apikey() is True
            assert self.handler.get_credentials() == (
                "ap-northeast-1",
                "plain-key",
            )
            assert self.handler.sync_apikey() is True

        assert self.manager.store_calls == 1
        assert self.manager.entries[AUTH_NAME] == "plain-key"
        assert self._read_qsettings()[APIKEY] == "plain-key"
        assert self.handler.apikey_sync_warning == APIKEY_CLEANUP_WARNING

        # Reload the plaintext and retry cleanup in a new session.
        self.handler.initialize_settings()
        assert self.handler.apikey_sync_warning is None
        assert self.handler.sync_apikey() is True
        assert self.manager.store_calls == 2
        assert APIKEY not in self._read_qsettings()

    def test_declined_migration_is_not_retried(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("plain-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        self.manager.locked = True

        assert self.handler.sync_apikey() is True
        assert self.handler.sync_apikey() is True
        assert self.manager.store_calls == 1
        assert self._read_qsettings()[APIKEY] == "plain-key"
        # The plaintext key remains usable when migration is declined.
        assert self.handler.get_credentials() == ("ap-northeast-1", "plain-key")

    def test_clearing_removes_both_stores(self):
        self.manager.entries[AUTH_NAME] = "old-key"
        self._set_state("old-key")
        assert self.handler.save_apikey("") is False
        assert AUTH_NAME not in self.manager.entries
        assert self.handler.get_setting(APIKEY) == ""

    def test_clearing_succeeds_when_nothing_is_stored(self):
        self._set_state("")
        assert self.handler.save_apikey("") is False

    def test_clearing_without_auth_warns_and_retries_encrypted_storage(self):
        self.manager.entries[AUTH_NAME] = "encrypted-key"
        self._write_qsettings({APIKEY: "plain-key"})
        self._set_state("plain-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)

        assert self.handler.save_apikey("") is False
        assert self.handler.get_setting(APIKEY) == ""
        assert APIKEY not in self._read_qsettings()
        assert self.handler._apikey_synced is False
        assert self.handler.apikey_sync_warning == APIKEY_REMOVAL_INCOMPLETE_WARNING

        # Once auth becomes available, expose the encrypted copy so the user
        # can request deletion again instead of silently treating it as cleared.
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        assert self.handler.sync_apikey() is True
        assert self.handler.get_setting(APIKEY) == "encrypted-key"
        assert self.handler.save_apikey("") is False
        assert AUTH_NAME not in self.manager.entries
        assert self.handler.apikey_sync_warning is None

    def test_failed_auth_removal_preserves_newer_plaintext(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("new-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        self.manager.entries[AUTH_NAME] = "old-key"
        self.manager.removeAuthSetting = lambda name: False

        with self.assertRaises(ConfigurationError):
            self.handler.save_apikey("")

        assert self.manager.entries[AUTH_NAME] == "old-key"
        assert self._read_qsettings()[APIKEY] == "new-key"
        assert self.handler.get_setting(APIKEY) == "new-key"

        # On restart, the newer plaintext replaces the stale auth entry.
        self.handler.initialize_settings()
        assert self.handler.sync_apikey() is True
        assert self.manager.entries[AUTH_NAME] == "new-key"
        assert APIKEY not in self._read_qsettings()

    def test_cleared_key_does_not_resurrect(self):
        self.manager.entries[AUTH_NAME] = "old-key"
        self._set_state("old-key")
        self.handler.save_apikey("")
        # Simulate a QGIS restart.
        self.handler.initialize_settings()
        self.handler.sync_apikey()
        assert not self.handler.get_setting(APIKEY)

    def test_save_updates_existing_plaintext_before_touching_auth(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("old-plain-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        self.manager.entries[AUTH_NAME] = "old-auth-key"

        with (
            patch.object(
                self.handler,
                "_write_qsettings_value",
                side_effect=ConfigurationError("settings failed"),
            ),
            self.assertRaises(ConfigurationError),
        ):
            self.handler.save_apikey("new-key")

        assert self.manager.store_calls == 0
        assert self.manager.entries[AUTH_NAME] == "old-auth-key"
        assert self._read_qsettings()[APIKEY] == "old-plain-key"
        assert self.handler.get_setting(APIKEY) == "old-plain-key"

    def test_qsettings_read_failure_does_not_touch_auth_or_plaintext(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("old-plain-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        self.manager.entries[AUTH_NAME] = "old-auth-key"
        qsettings = Mock()
        qsettings.status.side_effect = [SETTINGS_NO_ERROR, SETTINGS_ACCESS_ERROR]
        qsettings.contains.return_value = False

        with (
            patch(
                "location_service.utils.configuration_handler.QSettings",
                return_value=qsettings,
            ),
            self.assertRaises(ConfigurationError),
        ):
            self.handler.save_apikey("new-key")

        qsettings.sync.assert_called_once_with()
        qsettings.contains.assert_called_once_with(APIKEY)
        assert self.manager.store_calls == 0
        assert self.manager.entries[AUTH_NAME] == "old-auth-key"
        assert self._read_qsettings()[APIKEY] == "old-plain-key"
        assert self.handler.get_setting(APIKEY) == "old-plain-key"

    def test_save_cleanup_failure_keeps_both_stores_on_new_value(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("old-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)

        with patch.object(
            self.handler,
            "_remove_qsettings_value",
            side_effect=ConfigurationError("settings failed"),
        ):
            assert self.handler.save_apikey("new-key") is True
            assert self.handler.sync_apikey() is True

        assert self.manager.store_calls == 1
        assert self.manager.entries[AUTH_NAME] == "new-key"
        assert self._read_qsettings()[APIKEY] == "new-key"
        assert self.handler.get_setting(APIKEY) == "new-key"
        assert self.handler.apikey_sync_warning == APIKEY_CLEANUP_WARNING

        self.handler.initialize_settings()
        assert self.handler.sync_apikey() is True
        assert self.manager.store_calls == 2
        assert APIKEY not in self._read_qsettings()

    def test_qsettings_write_failure_keeps_memory_unchanged(self):
        self._set_state("old-key", region="old-region")
        qsettings = Mock()
        qsettings.status.return_value = SETTINGS_ACCESS_ERROR

        with (
            patch(
                "location_service.utils.configuration_handler.QSettings",
                return_value=qsettings,
            ),
            self.assertRaises(ConfigurationError),
        ):
            self.handler.save_region("us-east-1")

        qsettings.sync.assert_called_once_with()
        assert self.handler.get_setting(ConfigurationHandler.KEY_REGION) == "old-region"

    def test_clear_qsettings_failure_keeps_plaintext_and_memory(self):
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)
        self._set_state("")
        self.handler.save_apikey("old-key")
        ConfigurationHandler._auth_manager = staticmethod(lambda: self.manager)
        self.manager.entries[AUTH_NAME] = "old-key"
        qsettings = Mock()
        qsettings.status.return_value = SETTINGS_ACCESS_ERROR

        with (
            patch(
                "location_service.utils.configuration_handler.QSettings",
                return_value=qsettings,
            ),
            self.assertRaises(ConfigurationError),
        ):
            self.handler.save_apikey("")

        qsettings.sync.assert_called_once_with()
        assert AUTH_NAME not in self.manager.entries
        assert self._read_qsettings()[APIKEY] == "old-key"
        assert self.handler.get_setting(APIKEY) == "old-key"

    def test_save_region_does_not_read_a_root_qsettings_value(self):
        self._set_state("", region="old-region")
        qsettings = Mock()
        qsettings.status.return_value = SETTINGS_NO_ERROR
        qsettings.value.return_value = "unrelated-root-value"

        with patch(
            "location_service.utils.configuration_handler.QSettings",
            return_value=qsettings,
        ):
            self.handler.save_region("ap-northeast-1")

        qsettings.value.assert_not_called()
        assert (
            self.handler.get_setting(ConfigurationHandler.KEY_REGION)
            == "ap-northeast-1"
        )


@unittest.skipUnless(
    HAS_QGIS and isinstance(QApplication.instance(), QApplication),
    "A running QGIS application is required",
)
class TestConfigSave(unittest.TestCase):
    """Covers configuration saves and partial-failure reporting."""

    def setUp(self):
        with patch.object(config_module, "load_style"):
            self.dialog = ConfigUi()
        self.addCleanup(self.dialog.deleteLater)
        self.handler = Mock()
        self.handler.apikey_sync_warning = None
        self.dialog.configuration_handler = self.handler
        self.dialog.region_lineEdit.setText("us-east-1")
        self.dialog.apikey_lineEdit.setText("new-key")

    def test_reload_reports_plaintext_cleanup_warning(self):
        self.handler.sync_apikey.return_value = True
        self.handler.get_setting.side_effect = {
            ConfigurationHandler.KEY_REGION: "us-east-1",
            APIKEY: "stored-key",
        }.get
        self.handler.apikey_sync_warning = APIKEY_CLEANUP_WARNING

        with patch.object(config_module, "push_message") as push_message:
            self.dialog.reload_settings()

        push_message.assert_called_once_with(
            config_module.WARNING,
            APIKEY_CLEANUP_WARNING,
            duration=8,
        )

    def test_empty_unreadable_key_is_left_unchanged(self):
        self.handler.sync_apikey.return_value = False
        self.handler.get_setting.side_effect = {
            ConfigurationHandler.KEY_REGION: "us-east-1",
            APIKEY: "",
        }.get
        self.dialog.reload_settings()

        with (
            patch.object(config_module, "show_error") as show_error,
            patch.object(config_module, "push_message") as push_message,
        ):
            self.dialog._save()

        self.handler.save_apikey.assert_not_called()
        self.handler.save_region.assert_called_once_with("us-east-1")
        show_error.assert_not_called()
        assert (
            call(
                config_module.WARNING,
                "The API key was left unchanged: "
                "the authentication database is locked.",
                duration=8,
            )
            in push_message.call_args_list
        )

    def test_save_cleanup_warning_replaces_plaintext_fallback_hint(self):
        self.handler.save_apikey.return_value = True
        self.handler.apikey_sync_warning = APIKEY_CLEANUP_WARNING

        with patch.object(config_module, "push_message") as push_message:
            self.dialog._save()

        assert push_message.call_args_list == [
            call(config_module.SUCCESS, "Settings saved."),
            call(config_module.WARNING, APIKEY_CLEANUP_WARNING, duration=8),
        ]

    def test_successful_save_emits_settings_saved(self):
        settings_saved = Mock()
        self.dialog.settings_saved.connect(settings_saved)

        with patch.object(config_module, "push_message"):
            self.dialog._save()

        settings_saved.assert_called_once_with()

    def test_auth_unavailable_clear_warns_without_reporting_success(self):
        self.dialog.apikey_lineEdit.clear()
        self.handler.save_apikey.return_value = False
        self.handler.apikey_sync_warning = APIKEY_REMOVAL_INCOMPLETE_WARNING

        with (
            patch.object(config_module, "show_error") as show_error,
            patch.object(config_module, "push_message") as push_message,
        ):
            self.dialog._save()

        assert self.handler.method_calls == [
            call.save_apikey(""),
            call.save_region("us-east-1"),
        ]
        show_error.assert_not_called()
        push_message.assert_called_once_with(
            config_module.WARNING,
            APIKEY_REMOVAL_INCOMPLETE_WARNING,
            duration=8,
        )

    def test_apikey_failure_does_not_save_region(self):
        self.handler.save_apikey.side_effect = ConfigurationError("auth failed")
        settings_saved = Mock()
        self.dialog.settings_saved.connect(settings_saved)

        with (
            patch.object(config_module, "show_error") as show_error,
            patch.object(config_module, "push_message") as push_message,
        ):
            self.dialog._save()

        self.handler.save_region.assert_not_called()
        settings_saved.assert_not_called()
        push_message.assert_not_called()
        assert (
            "Some changes may already have been applied" in show_error.call_args.args[2]
        )

    def test_region_failure_reports_the_saved_plaintext_apikey(self):
        self.handler.save_apikey.return_value = True
        self.handler.save_region.side_effect = ConfigurationError("settings failed")

        with (
            patch.object(config_module, "show_error") as show_error,
            patch.object(config_module, "push_message") as push_message,
        ):
            self.dialog._save()

        assert self.handler.method_calls == [
            call.save_apikey("new-key"),
            call.save_region("us-east-1"),
        ]
        push_message.assert_not_called()
        assert (
            "API key was saved as plain text, but the region was not saved"
            in show_error.call_args.args[2]
        )

    def test_region_failure_reports_cleared_apikey(self):
        self.dialog.apikey_lineEdit.clear()
        self.handler.save_apikey.return_value = False
        self.handler.save_region.side_effect = ConfigurationError("settings failed")

        with (
            patch.object(config_module, "show_error") as show_error,
            patch.object(config_module, "push_message") as push_message,
        ):
            self.dialog._save()

        assert self.handler.method_calls == [
            call.save_apikey(""),
            call.save_region("us-east-1"),
        ]
        push_message.assert_not_called()
        assert (
            "API key was cleared, but the region was not saved"
            in show_error.call_args.args[2]
        )


if __name__ == "__main__":
    unittest.main()

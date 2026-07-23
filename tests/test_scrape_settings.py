import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from main import ScrapeSettingsDialog


class ScrapeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_media_options_have_expected_defaults(self):
        dialog = ScrapeSettingsDialog({})
        self.addCleanup(dialog.close)

        self.assertTrue(dialog.normalize_media_check.isChecked())
        self.assertFalse(dialog.anbernic_compatible_check.isChecked())
        settings = dialog.get_settings()
        self.assertTrue(settings['normalize_media_paths'])
        self.assertFalse(settings['anbernic_compatible'])

    def test_media_options_restore_saved_values(self):
        dialog = ScrapeSettingsDialog({
            'normalize_media_paths': False,
            'anbernic_compatible': True,
        })
        self.addCleanup(dialog.close)

        self.assertFalse(dialog.normalize_media_check.isChecked())
        self.assertTrue(dialog.anbernic_compatible_check.isChecked())


if __name__ == '__main__':
    unittest.main()

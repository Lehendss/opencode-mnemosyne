import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("check_storage", Path(__file__).with_name("check-storage.py"))
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class CheckStorageTest(unittest.TestCase):
    def test_accepts_existing_directory_through_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "data"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            self.assertEqual(storage.validate_path("database", str(link)), target.resolve())

    def test_rejects_broken_symlink_without_creating_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "missing"
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "missing or inaccessible"):
                storage.validate_path("database", str(link))
            self.assertFalse(target.exists())

    @patch.object(storage.os.path, "ismount", return_value=False)
    def test_rejects_unmounted_volume_before_accessing_data(self, ismount):
        with self.assertRaisesRegex(ValueError, "requires a mounted volume"):
            storage.validate_path("database", "/Volumes/Missing-Backup-SSD/database")
        ismount.assert_called_once_with("/Volumes/Missing-Backup-SSD")


if __name__ == "__main__":
    unittest.main()

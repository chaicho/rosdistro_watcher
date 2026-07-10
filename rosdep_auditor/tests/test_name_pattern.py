import unittest
import sys
import os

# Add parent directory to path for direct import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.name_pattern import clean_package_name, normalize_special_package_name


class TestNamePattern(unittest.TestCase):
    """Test package name cleaning functionality"""

    def test_clean_package_name(self):
        """Test clean_package_name with actual expected outputs"""
        self.assertEqual(clean_package_name("python39-sphinx"), "sphinx")
        self.assertEqual(clean_package_name("libwxgtk3.2-dev", remove_version=True, remove_symbols=True), "wxgtk")
        self.assertEqual(clean_package_name("qt6-5compat-dev"), "qt6-5compat")
        self.assertEqual(clean_package_name("qt6-5compat-dev", remove_version=True), "qt-compat")
        self.assertEqual(clean_package_name("libsdl1.2-compat-dev"), "sdl1.2")

    def test_normalize_special_package_name(self):
        """Test normalize_special_package_name with distribution-specific formats."""
        # OpenEmbedded layers
        self.assertEqual(normalize_special_package_name("python3-numpy@meta-python"), "python3-numpy")
        self.assertEqual(normalize_special_package_name("package@layer"), "package")

        # Gentoo categories
        self.assertEqual(normalize_special_package_name("dev-python/requests"), "requests")
        self.assertEqual(normalize_special_package_name("category/package"), "package")

        # Quotes
        self.assertEqual(normalize_special_package_name("'package'"), "package")

        # Wildcards
        self.assertEqual(normalize_special_package_name("package*"), "package")

        # Version suffixes
        self.assertEqual(normalize_special_package_name("package:1.0"), "package")

    def test_clean_package_name_distribution_specific(self):
        """Test clean_package_name with distribution_specific parameter."""
        # With distribution_specific=True (default)
        self.assertEqual(clean_package_name("python3-numpy@meta-python"), "numpy")
        # With distribution_specific=False, @layer is preserved until normalize_name removes it
        self.assertEqual(clean_package_name("python3-numpy@meta-python", distribution_specific=False), "numpy@meta-python")


if __name__ == '__main__':
    unittest.main()

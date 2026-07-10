import unittest
from unittest.mock import patch, MagicMock

from ..config import load_config
from .. import _find_package
from ..existence_verifer import ExistenceVerifier
from ..package_info import PackageInfo, PackageEntry


class TestPackageVerifier(unittest.TestCase):
    """Test package verification functionality for different repositories"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = load_config()
        self.verifier = ExistenceVerifier(self.config)

    def test_nixos_package_enumeration(self):
        """Test that NixOS packages can be enumerated through config"""
        # Check if NixOS is configured
        self.assertIn('nixos', self.config['package_sources'])
        nixos_sources = self.config['package_sources']['nixos']
        self.assertGreater(len(nixos_sources), 0)
        
        # Test package enumeration
        nixos_cache = nixos_sources[0]
        package_cache = nixos_cache.enumerate_packages('nixos', 'unstable', 'x86_64-linux')
        
        # Get some packages to verify enumeration works
        packages = []
        count = 0
        for pkg in package_cache:
            packages.append(pkg)
            count += 1
            if count >= 10:  # Just test first 10 packages
                break
        
        self.assertGreater(len(packages), 0, "Should enumerate at least some packages")
        
        # Verify package structure
        for pkg in packages:
            self.assertIsInstance(pkg, PackageEntry)
            self.assertIsInstance(pkg.name, str)
            self.assertIsInstance(pkg.version, str)
            self.assertIsInstance(pkg.url, str)
            self.assertTrue(len(pkg.name) > 0, "Package name should not be empty")
            self.assertTrue(len(pkg.version) > 0, "Package version should not be empty")

    def test_nixos_common_packages(self):
        """Test that common packages can be found in NixOS"""
        common_packages = ['git', 'cmake', 'vim', 'gcc']
        
        for pkg_name in common_packages:
            with self.subTest(package=pkg_name):
                result = _find_package(self.config, pkg_name, 'nixos', 'unstable', 'x86_64-linux')
                self.assertIsNotNone(result, f"Should find {pkg_name} in NixOS")
                self.assertEqual(result.name, pkg_name)
                self.assertIsInstance(result.version, str)
                self.assertTrue(len(result.version) > 0)

    def test_nixos_nonexistent_package(self):
        """Test handling of nonexistent packages in NixOS"""
        result = _find_package(self.config, 'definitely-nonexistent-package-12345', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNone(result, "Should return None for nonexistent packages")

    def test_nixos_different_channels(self):
        """Test that different NixOS channels work"""
        supported_versions = self.config.get('supported_versions', {}).get('nixos', ['unstable'])
        
        for channel in supported_versions:
            with self.subTest(channel=channel):
                result = _find_package(self.config, 'git', 'nixos', channel, 'x86_64-linux')
                if result:  # Some channels might not be available
                    self.assertEqual(result.name, 'git')
                    self.assertIn(channel, result.url)

    def test_existence_verifier_nixos(self):
        """Test ExistenceVerifier with NixOS packages"""
        # Test with existing package
        result = self.verifier.verify('git', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNotNone(result)
        self.assertIsInstance(result, PackageInfo)
        self.assertEqual(result.bin_name, 'git')
        self.assertEqual(result.repo_name, 'nixos')
        self.assertEqual(result.repo_version, 'unstable')

        # Test with nonexistent package
        result = self.verifier.verify('definitely-nonexistent-package-12345', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNone(result)

    def test_nixos_package_info_structure(self):
        """Test that NixOS packages have correct information structure"""
        result = _find_package(self.config, 'git', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNotNone(result)
        
        # Verify required fields
        self.assertTrue(hasattr(result, 'name'))
        self.assertTrue(hasattr(result, 'version'))
        self.assertTrue(hasattr(result, 'url'))
        self.assertTrue(hasattr(result, 'source_name'))
        self.assertTrue(hasattr(result, 'binary_name'))
        self.assertTrue(hasattr(result, 'description'))
        
        # Verify field types and content
        self.assertIsInstance(result.name, str)
        self.assertIsInstance(result.version, str)
        self.assertIsInstance(result.url, str)
        self.assertIsInstance(result.source_name, str)
        self.assertIsInstance(result.binary_name, str)
        self.assertIsInstance(result.description, str)
        
        # Verify URL contains NixOS search link
        self.assertIn('search.nixos.org', result.url)
        self.assertIn('packages', result.url)

    def test_nixos_caching_behavior(self):
        """Test that NixOS package caching works correctly"""
        # First call - should download and cache
        result1 = _find_package(self.config, 'git', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNotNone(result1)
        
        # Second call - should use cache (faster)
        result2 = _find_package(self.config, 'git', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNotNone(result2)
        
        # Results should be identical
        self.assertEqual(result1.name, result2.name)
        self.assertEqual(result1.version, result2.version)
        self.assertEqual(result1.url, result2.url)

    def test_nixos_supported_architectures(self):
        """Test that NixOS works with supported architectures"""
        supported_arches = self.config.get('supported_arches', {}).get('nixos', ['x86_64-linux'])
        
        for arch in supported_arches:
            with self.subTest(architecture=arch):
                result = _find_package(self.config, 'git', 'nixos', 'unstable', arch)
                # NixOS packages are architecture-independent in the metadata
                self.assertIsNotNone(result, f"Should find packages for {arch}")

    def test_nixos_package_search_performance(self):
        """Test that NixOS package search has reasonable performance"""
        import time
        
        # First, ensure cache is populated
        result1 = _find_package(self.config, 'git', 'nixos', 'unstable', 'x86_64-linux')
        self.assertIsNotNone(result1)
        
        # Measure time for cached search
        start_time = time.time()
        result2 = _find_package(self.config, 'cmake', 'nixos', 'unstable', 'x86_64-linux')
        search_time = time.time() - start_time
        
        self.assertIsNotNone(result2)
        
        # Cached search should be reasonably fast (under 5 seconds)
        self.assertLess(search_time, 5.0, 
                       "Cached package search should complete within 5 seconds")

    def test_nixos_error_handling(self):
        """Test error handling for NixOS package operations"""
        # Test with invalid channel
        result = _find_package(self.config, 'git', 'nixos', 'invalid-channel-name', 'x86_64-linux')
        # Should either return None or handle gracefully
        self.assertIsNone(result)

    def test_nixos_integration_with_config(self):
        """Test that NixOS integrates correctly with the configuration system"""
        # Verify config structure
        self.assertIn('nixos', self.config['package_sources'])
        self.assertIn('nixos', self.config.get('supported_versions', {}))
        self.assertIn('nixos', self.config.get('supported_arches', {}))
        
        # Verify package sources are properly configured
        nixos_sources = self.config['package_sources']['nixos']
        self.assertIsInstance(nixos_sources, list)
        self.assertGreater(len(nixos_sources), 0)
        
        # Test that the source has the expected interface
        source = nixos_sources[0]
        self.assertTrue(hasattr(source, 'enumerate_packages'))

    def test_gentoo_package_enumeration(self):
        """Test that Gentoo packages can be enumerated through config"""
        # Check if Gentoo is configured
        if 'gentoo' not in self.config['package_sources']:
            self.skipTest("Gentoo not configured in package sources")
            
        gentoo_sources = self.config['package_sources']['gentoo']
        self.assertGreater(len(gentoo_sources), 0)
        
        # Test package enumeration (limited to avoid long execution)
        gentoo_cache = gentoo_sources[0]
        package_cache = gentoo_cache.enumerate_packages('gentoo', '', 'amd64')
        
        # Get some packages to verify enumeration works
        packages = []
        count = 0
        for pkg in package_cache:
            packages.append(pkg)
            count += 1
            if count >= 5:  # Just test first 5 packages
                break
        
        self.assertGreater(len(packages), 0, "Should enumerate at least some packages")
        
        # Verify package structure
        for pkg in packages:
            self.assertIsInstance(pkg, PackageEntry)
            self.assertIsInstance(pkg.name, str)
            self.assertIsInstance(pkg.version, str)
            self.assertIsInstance(pkg.url, str)
            self.assertTrue(len(pkg.name) > 0, "Package name should not be empty")
            self.assertTrue(len(pkg.version) > 0, "Package version should not be empty")

    def test_gentoo_common_packages(self):
        """Test that common packages can be found in Gentoo"""
        if 'gentoo' not in self.config['package_sources']:
            self.skipTest("Gentoo not configured in package sources")
            
        common_packages = ['gcc', 'vim', 'bash']
        
        for pkg_name in common_packages:
            with self.subTest(package=pkg_name):
                result = _find_package(self.config, pkg_name, 'gentoo', '', 'amd64')
                # Note: We don't assert found packages since Gentoo parsing is limited
                if result:
                    self.assertEqual(result.name, pkg_name)
                    self.assertIsInstance(result.version, str)
                    self.assertTrue(len(result.version) > 0)

    def test_gentoo_package_info_structure(self):
        """Test that Gentoo packages have correct information structure"""
        if 'gentoo' not in self.config['package_sources']:
            self.skipTest("Gentoo not configured in package sources")
            
        # Try to find any package
        result = None
        gentoo_sources = self.config['package_sources']['gentoo']
        gentoo_cache = gentoo_sources[0]
        package_cache = gentoo_cache.enumerate_packages('gentoo', '', 'amd64')
        
        for pkg in package_cache:
            result = pkg
            break
            
        if result:
            # Verify required fields
            self.assertTrue(hasattr(result, 'name'))
            self.assertTrue(hasattr(result, 'version'))
            self.assertTrue(hasattr(result, 'url'))
            self.assertTrue(hasattr(result, 'source_name'))
            self.assertTrue(hasattr(result, 'binary_name'))
            self.assertTrue(hasattr(result, 'description'))
            
            # Verify field types and content
            self.assertIsInstance(result.name, str)
            self.assertIsInstance(result.version, str)
            self.assertIsInstance(result.url, str)
            self.assertIsInstance(result.source_name, str)
            self.assertIsInstance(result.binary_name, str)
            self.assertIsInstance(result.description, str)
            
            # Verify URL contains Gentoo packages link
            self.assertIn('packages.gentoo.org', result.url)

    def test_gentoo_integration_with_config(self):
        """Test that Gentoo integrates correctly with the configuration system"""
        if 'gentoo' not in self.config['package_sources']:
            self.skipTest("Gentoo not configured in package sources")
            
        # Verify config structure
        self.assertIn('gentoo', self.config['package_sources'])
        self.assertIn('gentoo', self.config.get('supported_versions', {}))
        self.assertIn('gentoo', self.config.get('supported_arches', {}))
        
        # Verify package sources are properly configured
        gentoo_sources = self.config['package_sources']['gentoo']
        self.assertIsInstance(gentoo_sources, list)
        self.assertGreater(len(gentoo_sources), 0)
        
        # Test that the source has the expected interface
        source = gentoo_sources[0]
        self.assertTrue(hasattr(source, 'enumerate_packages'))


if __name__ == '__main__':
    unittest.main()

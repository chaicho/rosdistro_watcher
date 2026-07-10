import unittest
import os
from typing import List, Dict, Set
from ..distro_parser import DistributionDatabase, RosdepDatabase, RosdepDatabaseEntry

class TestDistroParser(unittest.TestCase):

  def test_rosdep_parser_with_enriched_errors(self):
    """Test rosdep parser with comprehensive error reporting"""
    rosdep_db = RosdepDatabase()
    
    # Load YAML files with error handling
    try:
      with open('rosdep/base.yaml', 'r') as f:
        rosdep_db.load_yaml(f.read())
      with open('rosdep/python.yaml', 'r') as f:
        rosdep_db.load_yaml(f.read())
    except FileNotFoundError as e:
      self.fail(f"Required YAML file not found: {e}")
    except Exception as e:
      self.fail(f"Failed to load YAML files: {e}")
    
    all_entries = rosdep_db.get_all_entries()
    self.assertGreater(len(all_entries), 0, "No rosdep entries found in the database")
    
    invalid_entries = []
    entries_without_any_packages = []
    
    for entry_name in all_entries:
      entry = rosdep_db.get_entry(entry_name)
      self.assertIsNotNone(entry, f"Failed to retrieve entry object for '{entry_name}'")
      
      # Test entry validity
      if not entry.is_valid_entry():
        invalid_entries.append(self._analyze_entry_structure(entry_name, entry))
      
      # Test if entry has ANY packages at all
      has_any_packages = self._entry_has_any_packages(entry)
      if not has_any_packages:
        entries_without_any_packages.append(entry_name)
    
    # Assert with enriched error messages
    if invalid_entries:
      error_details = self._format_invalid_entries_error(invalid_entries)
      self.fail(f"Found {len(invalid_entries)} invalid entries:\n{error_details}")
    
  def _entry_has_any_packages(self, entry: 'RosdepDatabaseEntry') -> bool:
    """Check if an entry has any packages for any platform/version"""
    packages = entry.get_all_packages_from_config()
    if packages and len(packages) > 0:
      return True
    return False
  
  def _analyze_entry_structure(self, entry_name: str, entry: RosdepDatabaseEntry) -> Dict:
    """Analyze the structure of an invalid entry to provide detailed error information"""
    data = entry.get_data()
    error_info = {
      'entry_name': entry_name,
      'error_type': 'Invalid Structure',
      'platforms': list(data.keys()) if isinstance(data, dict) else f"Data is not dict: {type(data)}",
      'issues': []
    }
    
    if not isinstance(data, dict):
      error_info['issues'].append(f"Entry data is not a dictionary: {type(data)}")
      return error_info
    
    for platform, platform_data in data.items():
      if not isinstance(platform_data, dict):
        error_info['issues'].append(f"Platform '{platform}' data is not a dictionary: {type(platform_data)}")
        continue
      
      for version, version_data in platform_data.items():
        if not isinstance(version_data, dict):
          error_info['issues'].append(f"Version '{version}' data for platform '{platform}' is not a dictionary: {type(version_data)}")
          continue
        
        for pkg_manager, pkg_data in version_data.items():
          if not isinstance(pkg_data, dict):
            error_info['issues'].append(f"Package manager '{pkg_manager}' data for {platform}/{version} is not a dictionary: {type(pkg_data)}")
            continue
          
          if 'packages' not in pkg_data:
            error_info['issues'].append(f"Missing 'packages' key in {platform}/{version}/{pkg_manager}")
          elif set(pkg_data.keys()) != {'packages'}:
            error_info['issues'].append(f"Unexpected keys in {platform}/{version}/{pkg_manager}: {set(pkg_data.keys()) - {'packages'}}")
    
    return error_info
  

  
  def _format_invalid_entries_error(self, invalid_entries: List[Dict]) -> str:
    """Format invalid entries error for display"""
    error_lines = []
    for entry_info in invalid_entries:
      error_lines.append(f"  - {entry_info['entry_name']}:")
      if 'issues' in entry_info:
        for issue in entry_info['issues']:
          error_lines.append(f"    * {issue}")
      else:
        error_lines.append(f"    * {entry_info.get('error', 'Unknown error')}")
    return "\n".join(error_lines)
  


  def test_specific_entry_validation(self):
    """Test specific validation scenarios with detailed error reporting"""
    rosdep_db = RosdepDatabase()

    # Test with sample entry to ensure validation works
    test_entry_data = {
      'ubuntu': {
        '20.04': {
          'apt': {
            'packages': ['test-package']
          }
        }
      }
    }

    # Create a mock entry for testing
    from ..distro_parser import RosdepDatabaseEntry
    from ..config import load_config

    test_entry = RosdepDatabaseEntry('test-entry', test_entry_data, load_config())

    # Test valid entry
    self.assertTrue(test_entry.is_valid_entry(),
                   f"Valid test entry should pass validation. Data: {test_entry_data}")

    # Test invalid entries
    invalid_data_cases = [
      ({'invalid': 'not_a_dict'}, "Platform data should be a dictionary"),
      ({'ubuntu': {'20.04': 'not_a_dict'}}, "Version data should be a dictionary"),
      ({'ubuntu': {'20.04': {'apt': 'not_a_dict'}}}, "Package manager data should be a dictionary"),
      ({'ubuntu': {'20.04': {'apt': {'invalid_key': 'value'}}}}, "Package manager should only have 'packages' key"),
    ]

    for invalid_data, expected_error in invalid_data_cases:
      with self.subTest(data=invalid_data):
        invalid_entry = RosdepDatabaseEntry('invalid-entry', invalid_data, load_config())
        self.assertFalse(invalid_entry.is_valid_entry(),
                        f"Entry should be invalid: {expected_error}. Data: {invalid_data}")

  def test_package_details_none_returns(self):
    """Test get_package_details return values for null/non-existent cases"""
    from ..config import load_config

    rosdep_db = RosdepDatabase()

    # Load YAML files
    try:
      with open('rosdep/base.yaml', 'r') as f:
        rosdep_db.load_yaml(f.read())
      with open('rosdep/python.yaml', 'r') as f:
        rosdep_db.load_yaml(f.read())
    except FileNotFoundError as e:
      self.skipTest(f"Required YAML file not found: {e}")

    # Test: ubuntu: *: null returns {}
    entry = rosdep_db.get_entry('libav')
    self.assertIsNotNone(entry, "libav entry should exist")
    self.assertEqual(entry.get_package_details('ubuntu', '*'), {}, "ubuntu: *: null should return {}")

    # Test: non-existent platform returns None
    entry = rosdep_db.get_entry('aravis')
    self.assertIsNotNone(entry, "aravis entry should exist")
    self.assertIsNone(entry.get_package_details('osx', '*'), "osx doesn't exist for aravis, should return None")

    # Test: fedora: null (platform-level null) returns {}
    entry = rosdep_db.get_entry('apparmor')
    self.assertIsNotNone(entry, "apparmor entry should exist")
    self.assertEqual(entry.get_package_details('fedora', '*'), {}, "fedora: null should return {}")
    self.assertEqual(entry.get_package_details('fedora', '42'), {}, "fedora: null version should return {}")

    # Test: version null returns {}
    entry = rosdep_db.get_entry('aravis')
    # aravis has: ubuntu: {*, focal: ..., bionic: null}
    self.assertEqual(entry.get_package_details('ubuntu', 'bionic'), {}, "bionic: null should return {}")
    # trusty doesn't exist but falls back to * which has packages
    self.assertNotEqual(entry.get_package_details('ubuntu', 'trusty'), {}, "trusty should fall back to *")

    # Test: rhel: 8: null, 9: {pybind11-dev}
    entry = rosdep_db.get_entry('pybind11-dev')
    self.assertIsNotNone(entry, "pybind11-dev entry should exist")
    self.assertEqual(entry.get_package_details('rhel', '8'), {}, "rhel: 8: null should return {}")
    self.assertNotEqual(entry.get_package_details('rhel', '9'), {}, "rhel: 9 should have packages")
    self.assertEqual(entry.get_available_platform_versions_from_config('rhel'), ['9'], "rhel should only have version 9")

    # ========== Test None returns ==========
    # Case 1: Platform doesn't exist at all
    entry = rosdep_db.get_entry('aravis')
    self.assertIsNone(entry.get_package_details('windows', '*'), "windows not in platforms should return None")

    # Case 2: Version doesn't exist AND no * fallback
    # python3-posix-ipc has rhel: {'9': ...} but no *, so other versions return None
    entry = rosdep_db.get_entry('python3-posix-ipc')
    self.assertIsNotNone(entry, "python3-posix-ipc entry should exist")
    self.assertIsNone(entry.get_package_details('rhel', '8'), "rhel only has 9, no * fallback should return None")

    # Case 3: Platform doesn't exist in platforms (no global * fallback)
    entry = rosdep_db.get_entry('aravis')
    self.assertIsNone(entry.get_package_details('fedora', '42'), "fedora not in aravis platforms should return None")

    # Case 4: Query a package that definitely doesn't exist
    self.assertIsNone(rosdep_db.get_entry('nonexistent-package-xyz-123'), "non-existent entry should return None")

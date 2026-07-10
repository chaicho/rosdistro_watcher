#!/usr/bin/env python3

from typing import Dict, List, Any, Optional
import yaml
import re
from .tools import logger
from .config import load_config


class YamlEntryGenerator:
    """Generate rosdep YAML entries from package_distribution data."""

    def __init__(self, config=None):
        self.config = config or load_config()

        self.distribution_mappings = {
            'ubuntu': 'ubuntu',
            'debian': 'debian',
            'fedora': 'fedora',
            'centos': 'rhel',
            'rhel': 'rhel',
            'opensuse': 'opensuse',
            'arch': 'arch',
            'gentoo': 'gentoo',
            'nixos': 'nixos',
            'alpine': 'alpine',
            'freebsd': 'freebsd',
            'osx': 'osx',
            'macports': 'macports',
            'openembedded': 'openembedded'
        }

    def generate_entries(self, package_distribution: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Generate YAML entries.

        Args:
            package_distribution: Package distribution information

        Returns:
            Dictionary containing rosdep entries
        """
        if not package_distribution:
            return {}

        rosdep_key = self._get_rosdep_key(package_distribution)
        if not rosdep_key:
            logger.warning("Cannot determine rosdep key")
            return {}

        entry_data = self._build_mixed_entry_structure(package_distribution)

        if not entry_data:
            return {}

        return {rosdep_key: entry_data}

    def _get_rosdep_key(self, package_distribution: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Get rosdep key, prioritizing ubuntu package names."""
        ubuntu_keys = [
            'ubuntu',
            'ubuntu_jammy',
            'ubuntu_focal',
            'ubuntu_bionic',
            'ubuntu_noble'
        ]

        for key in ubuntu_keys:
            if key in package_distribution:
                repo_info = package_distribution[key]
                package_name = repo_info.get('package_name') or repo_info.get('bin_name')
                if package_name:
                    return package_name

        for repo_info in package_distribution.values():
            package_name = repo_info.get('package_name') or repo_info.get('bin_name')
            if package_name:
                return package_name

        return None

    def _has_pypi_repo(self, package_distribution: Dict[str, Dict[str, Any]]) -> bool:
        """Check if package distribution contains pypi repository."""
        for repo_key, repo_info in package_distribution.items():
            if repo_key.lower() == 'pypi':
                return True

            if repo_info.get('source') == 'pypi':
                return True

            metadata = repo_info.get('metadata', {})
            if 'pip' in metadata or 'pypi' in metadata:
                return True

        return False

    def _build_mixed_entry_structure(self, package_distribution: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Build mixed entry structure: each OS prefers system packages, falls back to pip."""
        entry = {}
        pip_package_name = self._get_pip_package_name(package_distribution)
        has_pypi = self._has_pypi_repo(package_distribution)

        system_packages = {}
        all_os_in_distribution = set()

        for repo_key, repo_info in package_distribution.items():
            if repo_key.lower() == 'pypi' or repo_info.get('source') == 'pypi':
                continue

            rosdep_os = self._get_rosdep_os_name(repo_key)
            if not rosdep_os:
                continue

            all_os_in_distribution.add(rosdep_os)

            system_package = self._get_system_package_name(repo_info)
            if system_package:
                version_info = self._extract_version_from_key(repo_key)

                if rosdep_os not in system_packages:
                    system_packages[rosdep_os] = {}

                if version_info['version']:
                    system_packages[rosdep_os][version_info['version']] = [system_package]
                else:
                    if 'general' not in system_packages[rosdep_os]:
                        system_packages[rosdep_os]['general'] = [system_package]
                    else:
                        if system_package not in system_packages[rosdep_os]['general']:
                            system_packages[rosdep_os]['general'].append(system_package)

        for os_name, os_packages in system_packages.items():
            if 'general' in os_packages and len(os_packages) == 1:
                entry[os_name] = os_packages['general']
            elif 'general' in os_packages:
                entry[os_name] = {}
                for version, packages in os_packages.items():
                    if version != 'general':
                        entry[os_name][version] = packages
                if self._all_versions_same_with_general(os_packages):
                    entry[os_name] = os_packages['general']
            else:
                if len(os_packages) == 1:
                    entry[os_name] = list(os_packages.values())[0]
                elif self._all_versions_same(os_packages):
                    entry[os_name] = list(os_packages.values())[0]
                else:
                    entry[os_name] = os_packages

        if pip_package_name and has_pypi:
            if not all_os_in_distribution:
                supported_os_list = [os_name for os_name in self.config.get('supported_versions', {}).keys()
                                   if os_name not in ['pypi', 'ros']]
                for os_name in supported_os_list:
                    entry[os_name] = {
                        'pip': {
                            'packages': [pip_package_name]
                        }
                    }
            else:
                supported_os_list = [os_name for os_name in self.config.get('supported_versions', {}).keys()
                                   if os_name not in ['pypi', 'ros']]
                for os_name in supported_os_list:
                    if os_name not in entry:
                        entry[os_name] = {
                            'pip': {
                                'packages': [pip_package_name]
                            }
                        }

        return entry

    def _all_versions_same_with_general(self, os_packages: Dict[str, List[str]]) -> bool:
        """Check if all version packages (including general) are the same."""
        if 'general' not in os_packages:
            return False
        
        general_packages = str(os_packages['general'])
        
        for version, packages in os_packages.items():
            if version != 'general' and str(packages) != general_packages:
                return False
        
        return True

    def _get_rosdep_os_name(self, repo_key: str) -> Optional[str]:
        """Extract rosdep OS name from repo_key."""
        os_name = repo_key.split('_')[0].lower()
        return self.distribution_mappings.get(os_name)

    def _extract_version_from_key(self, repo_key: str) -> Dict[str, Optional[str]]:
        """Extract version information from repo_key."""
        parts = repo_key.split('_')
        if len(parts) > 1:
            return {
                'os': parts[0].lower(),
                'version': parts[1].lower()
            }
        return {
            'os': parts[0].lower(),
            'version': None
        }

    def _get_system_package_name(self, repo_info: Dict[str, Any]) -> Optional[str]:
        """Get system package name."""
        return repo_info.get('package_name') or repo_info.get('bin_name')

    def _get_pip_package_name(self, package_distribution: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Get pip package name."""
        for repo_key, repo_info in package_distribution.items():
            if repo_key.lower() == 'pypi':
                return repo_info.get('package_name') or repo_info.get('bin_name')

            if repo_info.get('source') == 'pypi':
                return repo_info.get('package_name') or repo_info.get('bin_name')

            metadata = repo_info.get('metadata', {})
            if 'pip_name' in metadata:
                return metadata['pip_name']
            if 'pypi_name' in metadata:
                return metadata['pypi_name']

        for repo_info in package_distribution.values():
            package_name = repo_info.get('package_name') or repo_info.get('bin_name')
            if package_name:
                pip_name = re.sub(r'^python3?-', '', package_name)
                pip_name = re.sub(r'-dev$', '', pip_name)
                pip_name = pip_name.replace('-', '_')
                return pip_name

        return None

    def _all_versions_same(self, os_entry: Dict[str, Any]) -> bool:
        """Check if all versions are the same."""
        if not os_entry:
            return True
        
        values = list(os_entry.values())
        first_value = str(values[0])
        
        return all(str(v) == first_value for v in values)

    def format_for_yaml(self, entry_data: Dict[str, Any]) -> str:
        """Format as YAML string."""
        if not entry_data:
            return ""

        yaml_str = yaml.dump(
            entry_data,
            default_flow_style=False,
            sort_keys=True,
            indent=2,
            width=100
        )

        return yaml_str.strip()

    def validate_entry(self, entry_data: Dict[str, Any]) -> List[str]:
        """Validate generated entries."""
        issues = []

        if not entry_data:
            issues.append("Entry is empty")
            return issues

        for rosdep_key, entry in entry_data.items():
            if not isinstance(entry, dict):
                issues.append(f"Entry {rosdep_key} is not a dict")
                continue

            for distro, distro_data in entry.items():
                if distro not in self.distribution_mappings.values() and distro not in ['*']:
                    issues.append(f"Unknown distribution: {distro}")

                if not self._validate_distro_data(distro_data):
                    issues.append(f"Invalid data: {rosdep_key}.{distro}")

        return issues

    def _validate_distro_data(self, data: Any) -> bool:
        """Validate distribution data structure."""
        if isinstance(data, list):
            return all(isinstance(item, str) for item in data)
        elif isinstance(data, dict):
            for key, value in data.items():
                if key == 'pip':
                    if not isinstance(value, dict) or 'packages' not in value:
                        return False
                    if not isinstance(value['packages'], list):
                        return False
                else:
                    if not self._validate_distro_data(value):
                        return False
            return True
        elif data is None:
            return True
        else:
            return False



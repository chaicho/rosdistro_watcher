from typing import List, Dict, Optional, Set, Any
from .package_info import PackageInfo

class DistributionTable:
    """A class for storing and querying package distribution information."""
    def __init__(self, packages: List[PackageInfo]):
        self._table: Dict[str, Set[PackageInfo]] = self._build_table(packages)

    def _build_table(self, packages: List[PackageInfo]) -> Dict[str, Set[PackageInfo]]:
        """Build distribution table mapping to PackageInfo object sets."""
        distribution = {}
        for package in packages:
            repo_key = self._get_repo_key(package.repo_name, package.repo_version)
            if repo_key not in distribution:
                distribution[repo_key] = set()
            distribution[repo_key].add(package)
        return distribution

    def _get_repo_key(self, repo_name: str, repo_version: Optional[str]) -> str:
        """Generate unique key based on repository name and version."""
        if repo_name == "pypi" or repo_name == 'gentoo' or repo_name == 'arch':
            return repo_name
        if repo_version and repo_version.strip():
            return f"{repo_name}_{repo_version}"
        return repo_name

    def get_packages_info(self, repo_name: str, repo_version: Optional[str] = None) -> Set[PackageInfo]:
        """Get set of package information for specified repository.

        Args:
            repo_name: Repository name
            repo_version: Repository version (optional)

        Returns:
            Set[PackageInfo]: Set containing package information, empty set if not found.
        """
        repo_key = self._get_repo_key(repo_name, repo_version)
        return self._table.get(repo_key, set())

    def get_first_package_info(self, repo_name: str, repo_version: Optional[str] = None) -> Optional[PackageInfo]:
        """Get first package info from specified repository (for backward compatibility).

        Args:
            repo_name: Repository name
            repo_version: Repository version (optional)

        Returns:
            PackageInfo: First package info object, or None if not found.
        """
        package_set = self.get_packages_info(repo_name, repo_version)
        result = next(iter(package_set)) if package_set else None
        if not result:
          ros_package_set = self.get_packages_info('ros', repo_version)
          result = next(iter(ros_package_set)) if ros_package_set else None
        return result

    def get_package_count(self, repo_name: str, repo_version: Optional[str] = None) -> int:
        """Get package count in specified repository.

        Args:
            repo_name: Repository name
            repo_version: Repository version (optional)

        Returns:
            int: Number of packages
        """
        package_set = self.get_packages_info(repo_name, repo_version)
        return len(package_set)

    def get_all_packages(self) -> List[PackageInfo]:
        """Return list of all package information."""
        all_packages = []
        for package_set in self._table.values():
            all_packages.extend(package_set)
        return all_packages

    def get_total_package_count(self) -> int:
        """Return total package count across all repositories."""
        total_count = 0
        for package_set in self._table.values():
            total_count += len(package_set)
        return total_count

    def add_distribution_table(self, other: 'DistributionTable') -> None:
        """Merge another DistributionTable's content into current table.

        Args:
            other: DistributionTable object to merge
        """
        for repo_key, package_set in other.items():
            if repo_key not in self._table:
                self._table[repo_key] = set()
            self._table[repo_key].update(package_set)

    def __getitem__(self, key: str) -> Set[PackageInfo]:
        """Allow direct access to package info set by key."""
        return self._table[key]

    def __iter__(self):
        """Allow iteration over repository keys."""
        return iter(self._table)

    def keys(self):
        return self._table.keys()

    def values(self):
        """Return iterator over all package info sets."""
        return self._table.values()

    def items(self):
        """Allow (key, value) pair iteration like a dictionary."""
        return self._table.items()

    def __len__(self):
        return len(self._table)

    def __contains__(self, key: str):
        return key in self._table

    def __str__(self):
        string = "\n"
        for repo_key, package_set in self._table.items():
            string += f"{repo_key}:\n"
            string += "  " + "\n  ".join([package.bin_name for package in package_set]) + "\n"
        return string

    def to_rosdep_entry(self, rosdep_key: str = None, config: Dict = None):
        """Convert this DistributionTable to a RosdepDatabaseEntry.

        Args:
            rosdep_key: The name for the rosdep entry. If None, will use first package name
            config: Configuration dict. If None, will load default config

        Returns:
            RosdepDatabaseEntry: A rosdep entry object
        """
        from .distro_parser import RosdepDatabaseEntry
        from .config import load_config

        if config is None:
            config = load_config()

        all_packages = self.get_all_packages()
        if not all_packages:
            return RosdepDatabaseEntry('unknown', {}, config)

        # Simple key inference - just use first package
        if rosdep_key is None:
            first_pkg = all_packages[0]
            if first_pkg.metadata and 'rosdep_key' in first_pkg.metadata:
                rosdep_key = first_pkg.metadata['rosdep_key']
            else:
                rosdep_key = first_pkg.bin_name or first_pkg.name

        # Build rosdep data structure: platform -> version -> package_manager -> {packages: [...]}
        rosdep_data = {}
        pip_packages = []

        for package in all_packages:
            platform = package.repo_name
            version = package.repo_version or '*'

            # Handle pypi specially
            if platform == 'pypi':
                pip_packages.append(package.bin_name)
                continue

            # Get package manager
            if package.metadata and 'package_manager' in package.metadata:
                package_manager = package.metadata['package_manager']
            else:
                # Simple defaults
                defaults = {'ubuntu': 'apt', 'debian': 'apt', 'fedora': 'dnf', 'rhel': 'yum', 'nixos': 'nix'}
                package_manager = defaults.get(platform, 'apt')

            # Build nested structure
            if platform not in rosdep_data:
                rosdep_data[platform] = {}
            if version not in rosdep_data[platform]:
                rosdep_data[platform][version] = {}
            if package_manager not in rosdep_data[platform][version]:
                rosdep_data[platform][version][package_manager] = {'packages': []}

            rosdep_data[platform][version][package_manager]['packages'].append(package.bin_name)

        # Add pip packages to '*' platform if any
        if pip_packages:
            rosdep_data['*'] = {'*': {'pip': {'packages': pip_packages}}}

        return RosdepDatabaseEntry(rosdep_key, rosdep_data, config)

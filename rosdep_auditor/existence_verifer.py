from .package_info import PackageInfo
from . import _find_package, _find_packages_with_filelist, _find_packages_with_description, _apply_name_replacements
from typing import List, Set
from .tools import logger

class ExistenceVerifier:
    """
    Existence verifier.

    Verifies whether a specified package name actually exists in the target software repository.
    """
    def __init__(self, config):
        self.config = config

    def verify(self, package_name: str, repo_name: str, repo_version: str = "", repo_arch: str = "") -> PackageInfo:
        """Verify if package exists.

        Args:
            package_name: Package name
            repo_name: Repository name
            repo_version: Repository version (e.g., "20.04", "focal")
            repo_arch: Repository architecture (e.g., "amd64", "arm64")

        Returns:
            PackageInfo: Package information if exists, None otherwise
        """
        # Use the same find_package method as problem_detector
        package_name = _apply_name_replacements(self.config, package_name, repo_name, repo_version)
        package_entry = _find_package(self.config, package_name, repo_name, repo_version, repo_arch)
        if package_entry is None:
            return None
        result = PackageInfo.from_package_entry(package_entry, repo_name, repo_version)
        return result

    def verify_and_update_package_info(self, package_info: PackageInfo) -> PackageInfo:
        """Verify if package exists and update package information."""
        package_entry = _find_package(self.config, package_info.bin_name, package_info.repo_name, package_info.repo_version, package_info.arch)
        if package_entry is None:
            return None
        result = PackageInfo.from_package_entry(package_entry, package_info.repo_name, package_info.repo_version)
        return result

    def verify_with_filelist(self, file_list: List[str], repo_name: str, repo_version: str = "", repo_arch: str = "") -> Set[PackageInfo]:
        """Verify if packages with given file list exist."""
        package_entries = _find_packages_with_filelist(self.config, file_list, repo_name, repo_version, repo_arch)
        if not package_entries:
            return set()
        results = set()
        for package_entry in package_entries:
            result = PackageInfo.from_package_entry(package_entry, repo_name, repo_version)
            results.add(result)
        return results

    def verify_with_description(self, description: str, repo_name: str, repo_version: str = "", repo_arch: str = "") -> Set[PackageInfo]:
        """Verify if packages with given description exist."""
        package_entries = _find_packages_with_description(self.config, description, repo_name, repo_version, repo_arch)
        if not package_entries:
            return set()
        results = set()
        for package_entry in package_entries:
            result = PackageInfo.from_package_entry(package_entry, repo_name, repo_version)
            results.add(result)
        return results

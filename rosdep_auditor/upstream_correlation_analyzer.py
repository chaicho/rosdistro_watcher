#!/usr/bin/env python3

from typing import Set
from .package_info import PackageInfo
from .upstream_mapper import RepologyPackageMapper


class SourceCorrelationAnalyzer:
    """
    Source correlation analyzer.

    Traces the original source package for a given package and identifies related packages.
    Uses Repology access method consistent with problem_detector.
    """
    def __init__(self, config):
        self.config = config
        self.repology_mapper = RepologyPackageMapper(config)

    def analyze(self, package_name: str, repo_name: str, repo_version: str = "") -> Set[PackageInfo]:
        """Analyze source correlation.

        Args:
            package_name: Package name
            repo_name: Repository name
            repo_version: Repository version

        Returns:
            Set[PackageInfo]: Set of related package information
        """
        package_name = package_name.replace('_', '-')
        related_packages = self.repology_mapper.map_package_distributions(repo_name, repo_version, package_name)

        # Convert to set if it's not already
        if isinstance(related_packages, list):
            return set(related_packages)
        return related_packages

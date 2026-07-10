#!/usr/bin/env python3

from typing import List, Set
import re
from .package_info import PackageInfo
from .existence_verifer import ExistenceVerifier
from .tools import logger
from .tools.name_pattern import generate_name_variants




class CrossRepositoryNameMatcher:
    """
    Cross-repository name matcher.

    Searches for potential corresponding binary packages in other software repositories
    by intelligently constructing name variants.
    Uses configuration and verification methods consistent with problem_detector.
    """
    def __init__(self, config):
        self.config = config
        self.verifier = ExistenceVerifier(config)

    def find_matches(self, baseline_package: PackageInfo) -> Set[PackageInfo]:
        """Find matching packages.

        Args:
            baseline_package: Baseline package information

        Returns:
            Set[PackageInfo]: Set of matching package information
        """
        matches = set()

        baseline_file_list = []
        baseline_description = baseline_package.description if baseline_package.description else ""
        if baseline_package.filelist:
           baseline_file_list.extend(baseline_package.filelist)
           logger.debug(f"Baseline file list: {baseline_file_list}")

        # Generate name variants and verify
        variants = self._generate_name_variants(baseline_package)
        logger.debug(f"Generated {len(variants)} variants")
        logger.debug(f"Variants: {variants}")
        supported_versions = self.config.get('supported_versions', {})
        supported_arches = self.config.get('supported_arches', {})

        for repo in supported_versions.keys():
            for version in supported_versions.get(repo, []):
                for arch in supported_arches.get(repo, ['amd64']):
                    logger.debug(f"Verifying filelist of {baseline_package.bin_name} {baseline_file_list} in {repo} {version} {arch}")
                    file_list_matches = self.verifier.verify_with_filelist(baseline_file_list, repo, version, arch)
                    if file_list_matches:
                        logger.debug(f"Found {len(file_list_matches)} matches for {baseline_package.bin_name} in {repo} {version} {arch} with filelist")
                        for pkg in file_list_matches:
                            if pkg.metadata is None:
                                pkg.metadata = {}
                            pkg.metadata['source'] = ['filelist']
                        matches.update(file_list_matches)

                    if baseline_description and baseline_description != "" and baseline_description != "None" and baseline_description != "Unknown":
                        description_matches = self.verifier.verify_with_description(baseline_description, repo, version, arch)
                        if description_matches:
                            logger.debug(f"Found {len(description_matches)} matches for {baseline_package.bin_name} in {repo} {version} {arch} with description")
                            for pkg in description_matches:
                                if pkg.metadata is None:
                                    pkg.metadata = {}
                                pkg.metadata['source'] = ['description']
                            matches.update(description_matches)

                    for variant in variants:
                        logger.debug(f"Verifying {variant} in {repo} {version} {arch}")
                        package_info = self.verifier.verify(variant, repo, version, arch)
                        if package_info:
                            logger.debug(f"Found {variant} in {repo} {version} {arch}")
                            if package_info.metadata is None:
                                package_info.metadata = {}
                            package_info.metadata['source'] = ['name']
                            matches.add(package_info)



        return matches

    def _generate_name_variants(self, baseline_package: PackageInfo) -> List[str]:
        """Generate name variants using generate_name_variants from name_pattern.py."""
        package_name = baseline_package.bin_name
        return generate_name_variants(package_name)


    def _is_python_package(self, package_info: PackageInfo) -> bool:
        """Check if package is a Python package (has python prefix or from PyPI repository)."""
        # Check if repository name is PyPI related
        if package_info.repo_name and package_info.repo_name.lower() in ['pypi']:
            return True

        # Check if package name matches python package pattern
        package_name = package_info.bin_name
        if re.match(r'^python\d*-', package_name):
            return True

        return False

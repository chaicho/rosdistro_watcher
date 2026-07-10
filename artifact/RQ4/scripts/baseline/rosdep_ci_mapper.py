#!/usr/bin/env python3

import sys
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Union

# TOOL_PATH points to the main repo root for importing rosdep_auditor modules
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(TOOL_PATH))

# Import external rosdep_repo_check CI functions
ci_find_package = None
ci_make_suggestion = None
ROSDEP_CI_AVAILABLE = False

# rosdep_repo_check package lives alongside this file.
# Ensure parent directory is importable before importing submodules.
BASELINE_PATH = Path(__file__).parent
sys.path.insert(0, str(BASELINE_PATH))

try:
    from rosdep_repo_check import find_package as ci_find_package
    from rosdep_repo_check.suggest import make_suggestion as ci_make_suggestion
    ROSDEP_CI_AVAILABLE = True
except Exception as e:
    print(f"Warning: RosdepCI checker not available: {e}")
    ROSDEP_CI_AVAILABLE = False
    import traceback
    traceback.print_exc()

from rosdep_auditor.config import load_config
from rosdep_auditor.distribution_table import DistributionTable
from rosdep_auditor.package_info import PackageInfo
from rosdep_auditor.tools.logger import get_logger


class RosdepCIMapper:
    """
    Rosdep CI package verification mapper.
    
    This class provides an interface to rosdep's CI package checker,
    which directly verifies package existence in platform repositories.
    Similar to RepologyPackageMapper, it returns DistributionTable results.
    """
    
    def __init__(self):
        if not ROSDEP_CI_AVAILABLE:
            raise ImportError("RosdepCI checker is not available")
        
        # Load CI config only
        self.ci_config = load_config()
        self.logger = get_logger()
        
    def map_package_distributions(self, platform: str, os_version: str, package_name: str, 
                                config_only: bool = True, use_suggestions: bool = True) -> List[PackageInfo]:
        """
        Map a package across different Linux distributions using rosdep CI verification.
        
        Args:
            platform: Platform name (e.g., 'ubuntu')
            os_version: OS version (e.g., 'jammy', 'noble')
            package_name: Package name to check
            config_only: Whether to filter by config support (for compatibility with other mappers)
            use_suggestions: Whether to use rosdep CI suggestion heuristics when exact match fails
            
        Returns:
            List[PackageInfo]: Found packages
        """
        package_infos = []
        
        # Get target platforms to check from CI config
        if config_only:
            # Use all CI supported platforms
            target_platforms = self._get_all_ci_platforms()
        
        for os_name, os_ver in target_platforms:
            try:
                # Get first supported architecture
                supported_arches = self.ci_config.get('supported_arches', {}).get(os_name, ['amd64', 'x86_64'])
                if not supported_arches:
                    continue
                    
                os_arch = supported_arches[0]
                
                # Apply name replacements if configured
                actual_package_name = self._apply_name_replacements(package_name, os_name, os_ver)
                
                # 1. Try to find the exact package using rosdep CI
                found_pkg = ci_find_package(self.ci_config, actual_package_name, os_name, os_ver, os_arch)
                
                if found_pkg:
                    # Create PackageInfo from found package
                    pkg_info = PackageInfo(
                        name=getattr(found_pkg, 'name', actual_package_name),
                        bin_name=getattr(found_pkg, 'binary_name', actual_package_name),
                        src_name=getattr(found_pkg, 'source_name', actual_package_name),
                        repo_name=os_name,
                        repo_version=os_ver,
                        arch=os_arch,
                        version=getattr(found_pkg, 'version', 'unknown'),
                        description=f"Found by rosdep CI (exact) in {os_name} {os_ver}",
                        confidence=1.0
                    )
                    package_infos.append(pkg_info)
                    self.logger.debug(f"RosdepCI found exact match for {actual_package_name} in {os_name} {os_ver}")
                
                # 2. If not found and suggestions enabled, try rosdep CI suggestion heuristics
                elif use_suggestions:
                    try:
                        suggested_pkg = ci_make_suggestion(self.ci_config, actual_package_name, os_name)
                        if suggested_pkg:
                            # Create PackageInfo from suggested package
                            pkg_info = PackageInfo(
                                name=getattr(suggested_pkg, 'name', actual_package_name),
                                bin_name=getattr(suggested_pkg, 'binary_name', actual_package_name),
                                src_name=getattr(suggested_pkg, 'source_name', actual_package_name),
                                repo_name=os_name,
                                repo_version=os_ver,
                                arch=os_arch,
                                version=getattr(suggested_pkg, 'version', 'unknown'),
                                description=f"Found by rosdep CI (suggested) in {os_name} {os_ver}",
                            )
                            package_infos.append(pkg_info)
                            self.logger.debug(f"RosdepCI found suggestion for {actual_package_name} in {os_name} {os_ver}: {suggested_pkg.binary_name}")
                    except Exception as suggest_error:
                        self.logger.debug(f"RosdepCI suggestion failed for {actual_package_name} in {os_name} {os_ver}: {suggest_error}")
                    
            except Exception as e:
                self.logger.warning(f"Error checking {package_name} on {os_name} {os_ver}: {e}")
                continue
        
        return package_infos
    
    def get_rosdep_ci_distribution_result(self, platform: str, os_version: str, package_name: str) -> DistributionTable:
        """
        Get the distribution result from rosdep CI verification.
        
        Args:
            platform: Platform name
            os_version: OS version  
            package_name: Package name
            
        Returns:
            DistributionTable containing found packages
        """
        package_infos = self.map_package_distributions(platform, os_version, package_name)
        distribution_table = DistributionTable(package_infos)
        return distribution_table
    
    def _get_all_ci_platforms(self) -> List[Tuple[str, str]]:
        """
        Get all supported platforms from CI config.
        
        Returns:
            List of (os_name, os_version) tuples
        """
        platforms = []
        supported_versions = self.ci_config.get('supported_versions', {})
        
        for os_name, versions in supported_versions.items():
            for version in versions:
                platforms.append((os_name, version))
        
        return platforms
    
    def _apply_name_replacements(self, package_name: str, os_name: str, os_version: str) -> str:
        """
        Apply name replacements based on CI config.
        
        Args:
            package_name: Original package name
            os_name: OS name
            os_version: OS version
            
        Returns:
            Modified package name
        """
        actual_package_name = package_name
        name_replacements = self.ci_config.get('name_replacements', {}).get(os_name, {}).get(os_version, {})
        
        for needle, haystack in name_replacements.items():
            actual_package_name = actual_package_name.replace(needle, haystack)
        
        return actual_package_name
    
    def get_supported_platforms(self) -> List[Tuple[str, str]]:
        """
        Get list of supported (os_name, os_version) combinations.
        
        Returns:
            List of (os_name, os_version) tuples
        """
        return self._get_all_ci_platforms()
    
    def suggest_package(self, package_name: str, os_name: str) -> Optional[PackageInfo]:
        """
        Use rosdep CI suggestion heuristics to find alternative package names.
        
        Args:
            package_name: Package name to find suggestions for
            os_name: Operating system name
            
        Returns:
            PackageInfo if suggestion found, None otherwise
        """
        try:
            suggested_pkg = ci_make_suggestion(self.ci_config, package_name, os_name)
            if suggested_pkg:
                # Get OS version and arch
                os_version = self.ci_config['supported_versions'][os_name][-1]  # Use latest version
                os_arch = self.ci_config['supported_arches'][os_name][0]       # Use first arch
                
                pkg_info = PackageInfo(
                    name=getattr(suggested_pkg, 'name', package_name),
                    bin_name=getattr(suggested_pkg, 'binary_name', package_name),
                    src_name=getattr(suggested_pkg, 'source_name', package_name),
                    repo_name=os_name,
                    repo_version=os_version,
                    arch=os_arch,
                    version=getattr(suggested_pkg, 'version', 'unknown'),
                    description=f"Suggested by rosdep CI for {package_name} on {os_name}",
                    confidence=0.7
                )
                return pkg_info
        except Exception as e:
            self.logger.debug(f"Suggestion failed for {package_name} on {os_name}: {e}")
        
        return None


def test_rosdep_ci_mapper():
    """Test function for RosdepCIMapper"""
    if not ROSDEP_CI_AVAILABLE:
        print("RosdepCI mapper not available for testing")
        return
        
    try:
        mapper = RosdepCIMapper()
        print("✓ RosdepCIMapper initialized successfully")
        
        # Get supported platforms
        platforms = mapper.get_supported_platforms()
        print(f"✓ Found {len(platforms)} supported platforms")
        print(f"  Sample platforms: {platforms[:5]}")
        
        # Test with a common package
        test_package = "cmake"
        test_platform = "ubuntu"
        test_version = "jammy"
        
        # Test package mapping
        package_infos = mapper.map_package_distributions(test_platform, test_version, test_package)
        print(f"✓ Package mapping test for '{test_package}' completed")
        print(f"  Found {len(package_infos)} packages")
        
        # Test distribution table
        dist_table = mapper.get_rosdep_ci_distribution_result(test_platform, test_version, test_package)
        print(f"✓ Distribution table test completed")
        print(f"  Found in {len(dist_table)} repositories")
        
        for repo_key, packages in dist_table.items():
            print(f"  {repo_key}: {len(packages)} packages")
            for pkg in packages:
                print(f"    - {pkg.bin_name} (version: {pkg.version})")
        
        # Test suggestion functionality
        print(f"\n✓ Testing suggestion functionality...")
        test_missing_packages = ["libmissing-dev", "python3-nonexistent", "fake-package"]
        
        for missing_pkg in test_missing_packages:
            suggestion = mapper.suggest_package(missing_pkg, test_platform)
            if suggestion:
                print(f"  Suggestion for '{missing_pkg}': {suggestion.bin_name} (confidence: {suggestion.confidence})")
            else:
                print(f"  No suggestion found for '{missing_pkg}'")
        
        # Test with suggestions enabled in mapping
        print(f"\n✓ Testing package mapping with suggestions...")
        test_dev_package = "libexample-dev"  # This should trigger suggestion heuristics
        package_infos_with_suggestions = mapper.map_package_distributions(
            test_platform, test_version, test_dev_package, use_suggestions=True)
        print(f"  Found {len(package_infos_with_suggestions)} packages with suggestions enabled")
        
        for pkg in package_infos_with_suggestions:
            confidence_str = "exact" if pkg.confidence == 1.0 else f"suggested ({pkg.confidence})"
            print(f"    - {pkg.bin_name} ({confidence_str})")
                
    except Exception as e:
        print(f"✗ RosdepCIMapper test failed: {e}")
        import traceback
        traceback.print_exc()

#!/usr/bin/env python3

import requests
import json
import sys
from typing import Dict, List, Optional, Tuple, Union
import time
from .config import load_config
from .package_info import PackageInfo
from .existence_verifer import ExistenceVerifier
from .tools import logger
from .distribution_table import DistributionTable
from .tools.cache import load_json_cache, save_json_cache

class RepologyPackageMapper:
    """
    A dedicated class for mapping packages across different Linux distributions
    using the Repology API (https://repology.org/api/v1).
    
    This is completely separate from the repository search implementation.
    """
    
    def __init__(self, config: Dict = None):
        """Initialize the Repology package mapper."""
        self.base_url = "https://repology.org"
        self.api_base_url = f"{self.base_url}/api/v1"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'repoInvestigator/1.0 (https://github.com/chaicho/repoInvestigator)'
        })
        if config is None:
            self.config = load_config()
        else:
            self.config = config
        self.existence_verifier = ExistenceVerifier(self.config)
        # Initialize mappings
        self._init_platform_mappings()
        self._init_release_codenames()
    
    def _init_platform_mappings(self):
        """Initialize mappings between Repology repository names and distribution names."""
        # Map from repo name to distro name
        self.repo_to_distro = {
            # Ubuntu
            'ubuntu_24_04': 'ubuntu-24.04',
            'ubuntu_22_04': 'ubuntu-22.04',
            'ubuntu_20_04': 'ubuntu-20.04',
            'ubuntu_18_04': 'ubuntu-18.04',
            # Debian
            'debian_12': 'debian-12',
            'debian_11': 'debian-11',
            'debian_sid': 'debian-sid',
            # Fedora
            'fedora_38': 'fedora-38',
            'fedora_39': 'fedora-39',
            'fedora_40': 'fedora-40',
            'fedora_rawhide': 'rawhide',
            # RHEL/CentOS
            'centos_8': 'centos-8',
            'centos_9': 'centos-9',
            'rhel_8': 'rhel-8',
            'rhel_9': 'rhel-9',
        }
        
        # Map from distro name to repo name
        self.distro_to_repo = {v: k for k, v in self.repo_to_distro.items()}
    
    def _init_release_codenames(self):
        """Initialize mappings for distribution release codenames."""
        self.version_to_codename = {
            # Ubuntu
            '24.04': 'noble',
            '22.04': 'jammy',
            '20.04': 'focal',
            '18.04': 'bionic',
            # Debian
            '12': 'bookworm',
            '11': 'bullseye',
            'sid': 'sid',
            # Fedora - no standard codenames, use version numbers
            # RHEL - no standard codenames, use version numbers
        }
        # Add reverse mapping: codename -> version
        self.codename_to_version = {v: k for k, v in self.version_to_codename.items()}
    
    def _extract_repo_parts(self, repo_name: str) -> Dict[str, str]:
        """
        Extract information from a repository name.

        Args:
            repo_name: The repository name (e.g., 'ubuntu_20_04', 'nix_stable_24_05')

        Returns:
            Dict with keys 'repo_name', 'repo_version', 'codename'
        """
        result = {
            'repo_name': None,
            'repo_version': None,
            'codename': None
        }
        # Handle nix repos: nix_stable_XX_YY -> nixos XX.YY
        if repo_name.startswith('nix_'):
            parts = repo_name.split('_')
            result['repo_name'] = 'nixos'
            if len(parts) >= 4 and parts[1] == 'stable':
                # nix_stable_24_05 -> 24.05
                version = f"{parts[2]}.{parts[3]}"
                result['repo_version'] = version
            elif len(parts) >= 3:
                # nix_unstable_24_11 or similar
                version = f"{parts[2]}.{parts[3]}" if len(parts) > 3 else parts[2]
                result['repo_version'] = version
            else:
                result['repo_version'] = '_'.join(parts[1:])
            return result

        # Split repo name into parts
        if '_' in repo_name:
            parts = repo_name.split('_')
            result['repo_name'] = parts[0]
            if len(parts) > 1:
                result['repo_version'] = '_'.join(parts[1:])
                # For ubuntu and debian, add the codename
                if result['repo_name'] == 'ubuntu' or result['repo_name'] == 'debian':
                    version = result['repo_version'].replace('_', '.')
                    result['codename'] = self.version_to_codename.get(version)
        else:
            result['repo_name'] = repo_name

        return result
      
    def map_package_distributions(self, platform: str, os_version: str, package_name: str, 
                            config_only: bool = True, return_multiple_info: bool = False, verify_existence: bool = True) -> Union[List[PackageInfo], Tuple[List[PackageInfo], bool]]:
        """
        Map a package across different Linux distributions using Repology API.
        # Config only is used to check if the package is supported by the config.
        # If config_only is True, the function will only return the packages that are supported by the config.
        # If config_only is False, the function will return all the packages that are found in the Repology API.
        
        Args:
            platform: Platform name
            os_version: OS version
            package_name: Package name to search
            config_only: Whether to filter by config support
            return_multiple_info: If True, returns (package_infos, has_multiple_binnames)
        
        Returns:
            If return_multiple_info is False: List[PackageInfo]
            If return_multiple_info is True: Tuple[List[PackageInfo], bool]
        """
        if hasattr(self, 'codename_to_version') and os_version in self.codename_to_version:
            numeric_version = self.codename_to_version[os_version]
        else:
            numeric_version = os_version
        formatted_version = numeric_version.replace('.', '_') if numeric_version else ''
        if platform == 'nixos':
            if os_version == 'unstable':
                platform_str = 'nix_unstable'
            else:
                platform_str = f"nix_stable_{formatted_version}" if formatted_version else 'nix_stable'
        elif platform in ['pypi', 'arch', 'gentoo']:
            platform_str = platform
        else:
            platform_str = f"{platform}_{formatted_version}" if formatted_version else platform
        package_infos = []
        unique_binnames = set()
        binnames_dict = {}
        # Query Repology once - this returns a list of package dictionaries
        repology_result = self._query_repology_api_exact(platform_str, package_name)
        logger.debug(f"repology_result: {repology_result}")
        for pkg in repology_result:
            unique_binnames = set()
            repo = pkg.get('repo')
            if not repo:
                continue
            repo_parts = self._extract_repo_parts(repo)
            repo_name = repo_parts.get('repo_name')
            if repo_name == 'epel' or repo_name == 'almalinux' or repo_name == 'centos':
              repo_name = 'rhel'
            repo_version = repo_parts.get('codename') or repo_parts.get('repo_version') or ''

            if not repo_name:
              logger.debug(f"Invalid repo name: {repo}")
              continue
            if repo_name not in self.config['supported_versions']:
              logger.debug(f"Repo not supported by rosdep: {repo_name}, repo: {repo}")
              continue
            if config_only:
              if not repo_version in self.config['supported_versions'][repo_name]:
                logger.debug(f"Repo version not supported by rosdep: {repo_version}, repo: {repo}")
                continue
              
            arch = self.config['supported_arches'][repo_name][0]         
            binname = pkg.get('binname') 
            binnames = pkg.get('binnames')
            srcname = pkg.get('srcname')
            version = repo_parts.get('version') or ''
            description = pkg.get('summary') or 'unknown'
            logger.debug(f"repo_name: {repo_name}, repo_version: {repo_version}") 
            if repo_name == 'gentoo' or repo_name == 'nixos':
              binname = srcname
            if binname:
                unique_binnames.add(binname)
                if verify_existence:
                    package_info = self.existence_verifier.verify(binname, repo_name, repo_version, arch)
                    if package_info:
                        package_info.version = version
                        if package_info.metadata is None:
                            package_info.metadata = {}
                        package_info.metadata['source'] = ['upstream']
                        package_infos.append(package_info)
                    else:
                        logger.error(f"Package {binname} returned by repology but not found in {repo_name} {repo_version} {arch}")
                else:
                    package_info = PackageInfo(
                        name=binname,
                        bin_name=binname,
                        src_name=srcname,
                        repo_name=repo_name,
                        repo_version=repo_version,
                        arch=arch,
                        version=version,
                        description=description,
                        metadata={'source': ['upstream']})
                    package_infos.append(package_info)
            elif binnames:
              # Handle binnames which could be a list or comma-separated string
              if isinstance(binnames, str):
                  binnames_list = [name.strip() for name in binnames.split(',')]
              else:
                  binnames_list = binnames
              
              # Process each binname to handle cases where individual binname contains commas
              final_binnames = []
              for binname in binnames_list:
                  if ',' in binname:
                      final_binnames.extend([name.strip() for name in binname.split(',')])
                  else:
                      final_binnames.append(binname)
              
              for binname in final_binnames: 
                    unique_binnames.add(binname)
                    if return_multiple_info:
                      continue
                    if verify_existence:
                      if binname.endswith('-debuginfo') or binname.endswith('-debugsource'):
                        continue
                      package_info  = self.existence_verifier.verify(binname, repo_name, repo_version, arch)
                      if package_info:
                        package_info.version = version
                        # Mark as collected from repology
                        if package_info.metadata is None:
                            package_info.metadata = {}
                        package_info.metadata['source'] = ['upstream']
                        package_infos.append(package_info)
                      else:
                        logger.error(f"Package {binname} returned by repology but not found in {repo_name} {repo_version} {arch}")
                    else:
                      package_info = PackageInfo(
                        name=binname,
                        bin_name=binname,
                        src_name=srcname,
                        repo_name=repo_name,
                        repo_version=repo_version,
                        arch=arch,
                        version=version,
                        description=description,
                        metadata={'source': ['upstream']})
                      package_infos.append(package_info)
            if len(unique_binnames) > 1 and return_multiple_info:
              logger.info(f"Found multiple binnames for {package_name} in {repo_name} {repo_version} {arch}: {unique_binnames}")
              binnames_dict[repo] = unique_binnames
 
        if return_multiple_info:
            return package_infos, binnames_dict
        else:
            return package_infos
          
    def get_repology_distribution_result(self, platform: str, os_version: str, package_name: str) -> DistributionTable:
        """
        Get the distribution result from Repology API.
        """
        package_infos = self.map_package_distributions(platform, os_version, package_name)
        distribution_table = DistributionTable(package_infos)
        return distribution_table
    
    def _query_repology_api_exact(self, platform: str, package_name: str) -> Dict[str, Dict]:
        """
        Query the Repology API for package information using the exact URL format.

        Args:
            platform: The platform identifier (e.g., 'ubuntu_20_04')
            package_name: The package name to search for

        Returns:
            Dict[str, Dict]: Mapping of distributions to package information
        """
        # Check cache first
        cache_key = f"{platform}_{package_name}"
        cached_result = load_json_cache("repology", cache_key, subdir="repology")
        if cached_result is not None:
            logger.debug(f"Using cached Repology result for {cache_key}")
            return cached_result

        try:
            # Use the exact URL format provided
            url = f"{self.base_url}/tools/project-by?repo={platform}&name_type=binname&target_page=api_v1_project&noautoresolve=on&name={package_name}"
            logger.info(f"Querying Repology API: {url}")
            response = self.session.get(url)
            time.sleep(1.5)

            # This endpoint redirects to the API endpoint if a match is found
            if response.status_code == 200 and "api/v1/project/" in response.url:
                # Extract the project name from the URL
                project_name = response.url.split("/")[-1]

                # Now get the actual project data from the API
                api_url = f"{self.api_base_url}/project/{project_name}"
                api_response = self.session.get(api_url)

                if api_response.status_code == 200:
                    packages = api_response.json()
                    # Save to cache
                    save_json_cache("repology", cache_key, packages, subdir="repology")
                    return packages

            # If no match was found or there was an error, return empty dict
            return {}

        except requests.RequestException as e:
            logger.error(f"Connection error with Repology API: {str(e)}")
            return {}
        except json.JSONDecodeError:
            logger.error("Invalid JSON response from Repology API")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error querying Repology API: {str(e)}")
            return {}
    
    def format_result_in_config(self, result: Dict[str, Dict], keep_all_versions: bool = False) -> Dict[str, Dict]:
        """
        Limit the result according to the config.
        """
        formatted_result = {}
        supported_versions = self.config.get('supported_versions', {})
        
        for distro, versions_dict in result.items():
            if distro in supported_versions:
                formatted_result[distro] = {}
                
                # For each version key in the result, try to map it to supported versions
                for version_key, pkg_list in versions_dict.items():
                    matched_version = None
                    
                    if keep_all_versions:
                      matched_version = version_key
                    else:
                      # Try to match the version_key with supported versions
                      for supported_version in supported_versions[distro]:
                          if self._version_matches(distro, version_key, supported_version):
                              matched_version = supported_version
                              break
                    
                    if matched_version:
                        if matched_version not in formatted_result[distro]:
                            formatted_result[distro][matched_version] = []
                        
                        for pkg in pkg_list:
                            # Keep only srcname and binname
                            filtered_pkg = {
                                'srcname': pkg.get('srcname'),
                                'binname': pkg.get('binname')
                            }
                            formatted_result[distro][matched_version].append(filtered_pkg)
        
        return formatted_result
    
    def format_packages_listed_in_config(self, result: Dict[str, Dict], config: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        Limit the result according to the config.
        """
        formatted_result = {}
    
    def get_rosdep_compatible_version(self, version: str) -> str:
        """
        Get the rosdep compatible version.
        """
        if version in self.version_to_codename:
            return self.version_to_codename[version]
        return version
        
    def _version_matches(self, distro: str, repology_version: str, config_version: str, ) -> bool:
        """
        Check if a Repology version matches a config supported version.
        
        Args:
            distro: Distribution name (e.g., 'ubuntu', 'fedora')
            repology_version: Version from Repology (e.g., '20_04', '41', 'focal')
            config_version: Version from config (e.g., 'focal', '41')
        """
        # Direct match (e.g., 'focal' == 'focal')
        if repology_version == config_version:
            return True
        
        # Handle Ubuntu: convert numeric version to codename and vice versa
        if distro == 'ubuntu':
            # If config_version is a codename, check if repology_version converts to it
            if config_version in self.codename_to_version:
                numeric = self.codename_to_version[config_version].replace('.', '_')
                if repology_version == numeric:
                    return True
            # If repology_version is a codename, check if it matches
            if repology_version in self.version_to_codename:
                if self.version_to_codename[repology_version] == config_version:
                    return True
        
        # Handle Debian: similar to Ubuntu
        elif distro == 'debian':
            if config_version in self.codename_to_version:
                numeric = self.codename_to_version[config_version]
                if repology_version == numeric:
                    return True
            if repology_version in self.version_to_codename:
                if self.version_to_codename[repology_version] == config_version:
                    return True
        
        # Handle Fedora: numeric versions (e.g., '41' matches '41')
        elif distro == 'fedora':
            if repology_version == config_version:
                return True
        
        # Handle RHEL: numeric versions (e.g., '8' matches '8')
        elif distro == 'rhel':
            if repology_version == config_version:
                return True
        
        # Handle OpenSUSE: version numbers (e.g., '15.2' matches '15_2')
        elif distro == 'opensuse':
            # leap is stable version, so we need to check if the repology version is a stable version
            repology_version = repology_version.replace('leap', '')
            if repology_version.replace('_', '.') == config_version:
                return True
            if repology_version == config_version.replace('.', '_'):
                return True
        
        # Handle Arch: empty string version
        elif distro == 'arch':
            return True
        
        # Handle Alpine: 'edge' version
        elif distro == 'alpine':
            if config_version == 'edge' and repology_version in ['edge', 'unknown']:
                return True
        
        # Handle OpenEmbedded: 'master' version
        elif distro == 'openembedded':
            if config_version == 'master' and repology_version in ['master', 'unknown']:
                return True
        
        return False

    def get_binname_from_srcname(self, srcname: str, platform: str) -> str:
        """
        Get the binary package name from the source package name.
        """

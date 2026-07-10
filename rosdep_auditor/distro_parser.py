from sysconfig import get_platform
from typing import Dict, List, Set, Optional, Any
import yaml
from collections import defaultdict
from .config import load_config
from .tools import logger

class RosdepLookupError(Exception):
    pass

# This class is not elaborated with respect to the config, so '*' is kept as a version
class RosdepDatabaseEntry:
    """Represents a single rosdep entry with easy access methods"""
    def __init__(self, name: str, data: Dict, config: Dict):
        self.name = name
        self._data = data
        self._config = config
    
    def get_data(self) -> Dict:
        return self._data
      
    def _is_package_manager(self, key: str) -> bool:
        """Check if a key represents a known package manager"""
        known_package_managers = {
            'apt', 'pip', 'source', 'dnf', 'yum', 'portage', 'brew',
            'pacman', 'apt-cyg', 'pkg_add', 'zypper', 'port','slackware','homebrew','slackpkg','macports'
        }
        return key in known_package_managers

    def get_platforms(self) -> List[str]:
        """Get all platforms (OS names) supported by this entry"""
        return list(self._data.keys())

    def get_versions(self, platform: str) -> List[str]:
        """Get all versions available for a specific platform"""
        if platform not in self._data:
            return []
        
        platform_data = self._data[platform]
        if not isinstance(platform_data, dict):
            return []
            
        return list(platform_data.keys())

    def get_available_package_managers(self, platform: str, version: str = None) -> List[str]:
        """Get available package managers for platform/version"""
        if platform not in self._data:
            return []

        platform_data = self._data[platform]
        if not isinstance(platform_data, dict):
            return []

        # Get version-specific data
        if version:
            if version not in platform_data:
                return []
            version_data = platform_data[version]
            if not version_data:  # Handle null/empty case
                return []
            return [k for k in version_data.keys() if self._is_package_manager(k)]
        
        # If no version specified, try '*' first, then any available version
        if '*' in platform_data:
            default_data = platform_data['*']
            if default_data:
                return [k for k in default_data.keys() if self._is_package_manager(k)]
        
        # If no default, check other versions
        for version_key, version_data in platform_data.items():
            if version_key == '*':
                continue
            if version_data:
                return [k for k in version_data.keys() if self._is_package_manager(k)]
        
        # If no package managers found, return empty set
        return []
    
    def get_package_manager(self, platform: str, version: str) -> Optional[str]:
        """Get the package manager for a specific platform/version"""
        if platform not in self._data:
            if '*' in self._data: 
                platform = '*'
            else:
              return None

        platform_data = self._data[platform]
        if not isinstance(platform_data, dict):
            return None
        
        if version not in platform_data:
            if '*' in platform_data:
               version = '*'
            else:
                return None
            
          
        if len(platform_data[version].keys())  == 1:
            return list(platform_data[version].keys())[0]
        elif len(platform_data[version].keys()) > 1:
            return None
        else:
            logger.warning(f"No package managers found for {platform} {version}")
            return None
    
    # The function is not elaborated with respect to the config, so '*' is kept as a version. If no version is found, '*' is used.
    # Also for platform, '*' is kept as a platform. If no platform is found, '*' is used.
    # Return None if no package details are found. Return {} if the package is set to null.
    def get_package_details(self, platform: str, version: str) -> (Optional[Dict]):
        """Get package details for specific platform/version"""
        if platform not in self._data:
            if '*' in self._data:
                platform = '*'
            else:
                return None
        
        platform_data = self._data[platform]
        if not isinstance(platform_data, dict):
            return None

        # Get version-specific data
        if version and version in platform_data:
            if platform_data[version] is None:
                return {}
            return platform_data[version]
          
        # If no version specified, try '*' first
        if '*' in platform_data:
            if platform_data['*'] is None:
                return {}
            return platform_data['*']
        
        # If no default version, return None
        return None
    
    def get_platform_and_version(self, platform: str, version: str) :
        """Get entry data for a specific platform and version"""
        """ This is used to handle the problem where '*' is used to represent all platforms or versions"""
        entry_platform = platform
        entry_version = version
        if platform not in self._data:
            if '*' in self._data:
                entry_platform = '*' 
            else:
                return None, None
              
        platform_data = self._data[entry_platform]
        
        if version not in platform_data:
            if '*' in platform_data:
                entry_version = '*'
            else:
                return None, None
              
        return entry_platform, entry_version
    
    def get_available_platforms_from_config(self, config: Dict = None) -> List[str]:
        """Get all available platforms for a specific entry according to the config"""
        if config is None:
            config = self._config
        platform_keys = self.get_platforms()
        if '*' in platform_keys:
          return list(config['supported_versions'].keys())
        else:
          # Intersect platform_keys and config['supported_versions'].keys()
          return list(set(platform_keys) & set(config['supported_versions'].keys()))
        
    def get_available_platform_versions_from_config(self, platform: str, config: Dict = None) -> List[str]:
        """Get all versions for a specific platform"""
        if config is None:
            config = self._config
        supported_versions = config['supported_versions'].get(platform, [])
        available_versions = [] 
        for version in supported_versions:
          package_details = self.get_package_details(platform, version)
          if package_details is not None and package_details != {}:
            available_versions.append(version)      
        return available_versions
    
    # The packages are normalized to the standard 4-level structure:
    # platform -> version -> package_manager -> {packages: [...]}
    def get_all_packages_from_config(self, config: Dict = None) -> List[Dict]:
        """Get all packages from the config"""
        result = []
        # Iterate through all platforms in this entry
        if config is None:
            config = self._config
            
        for platform in self.get_available_platforms_from_config(config):

            # Skip platforms that don't have package sources configured, but allow them for validation
            # We'll still process the entry even if package sources aren't configured
            has_package_sources = platform in config['package_sources']
                
            # Get all versions for this platform
            versions = self.get_available_platform_versions_from_config(platform, config)
            
            # Add specific versions
            for os_ver in versions:
                if os_ver == '*':
                    continue
                if os_ver in config['supported_versions'].get(platform, []):
                    package_details = self.get_package_details(platform, os_ver)
                    if package_details is not None and package_details != {}:
                        result.append({
                            'platform': platform,
                            'version': os_ver,
                            'package_manager': list(package_details.keys())[0],
                            'packages': list(package_details.values())[0]['packages']
                        })
                    elif package_details == None:
                      logger.error(f"Package details is None for {platform} {os_ver}")
                      # assert 0
                      
        return result
    def get_all_packages_with_package_manager(self, package_manager: str) -> List[str]:
        """Get all platforms and corresponding versions with a specific package manager"""
        result = []
        packages = self.get_all_packages_from_config()
        for package in packages:
          if package['package_manager'] == package_manager:
            result.append(package)
        return result
    
    def get_all_package_names(self) -> Dict[str, Set[tuple]]:
        """
        Get all package names from this entry with their context.
        This is config-independent and only follows the data structure rules.
        
        Returns:
            Dict[str, Set[tuple]]: package_name -> set of (platform, version, pkg_manager) tuples
        """
        result = {}
        
        # Traverse the 4-level structure: platform -> version -> package_manager -> {packages: [...]}
        for platform, platform_data in self._data.items():
            if not isinstance(platform_data, dict):
                continue
                
            for version, version_data in platform_data.items():
                if not isinstance(version_data, dict):
                    continue
                    
                for pkg_manager, pkg_manager_data in version_data.items():
                    if not isinstance(pkg_manager_data, dict):
                        continue
                        
                    packages = pkg_manager_data.get('packages', [])
                    if isinstance(packages, str):
                        packages = [packages]
                    elif not isinstance(packages, list):
                        continue
                    
                    for pkg in packages:
                        if pkg:
                            if pkg not in result:
                                result[pkg] = set()
                            result[pkg].add((platform, version, pkg_manager))
        
        return result
    
    def is_valid_entry(self) -> bool:
        """Check if the entry is valid"""
        # The data should be a four level dict
        # The first level is the platform
        # The second level is the version
        # The third level is the package_manager
        # The fourth level is the packages
        
        def is_four_level_dict(d):
            if not isinstance(d, dict):
                return False
            for v1 in d.values():
                if not isinstance(v1, dict):
                    return False
                for v2 in v1.values():
                    if not isinstance(v2, dict):
                        return False
                    for v3 in v2.values():
                        if not isinstance(v3, dict):
                            return False
                        # Check if 'packages' key exists (required)
                        if 'packages' not in v3:
                            return False
                        # Allow 'packages' key and optionally ignore other keys like 'depends'
                        # The main requirement is that 'packages' exists and is properly formatted
            return True

        return is_four_level_dict(self._data)
    
    def to_distribution_table(self):
        """
        Convert this RosdepDatabaseEntry to a DistributionTable.
        
        Returns:
            DistributionTable: A table containing PackageInfo objects extracted from this rosdep entry
        """
        from .distribution_table import DistributionTable
        from .package_info import PackageInfo
        
        packages = []
        rosdep_key = self.name
        
        # Use the get_all_packages_from_config method to get structured package data
        try:
            package_configs = self.get_all_packages_from_config()
            
            # Convert each package config to PackageInfo
            for pkg_config in package_configs:
                platform = pkg_config.get('platform')
                version = pkg_config.get('version') 
                package_manager = pkg_config.get('package_manager')
                package_list = pkg_config.get('packages', [])
                
                if not package_list:
                    continue
                    
                # Ensure package_list is a list
                if isinstance(package_list, str):
                    package_list = [package_list]
                elif not isinstance(package_list, list):
                    continue
                
                # Create PackageInfo objects for each package
                for package_name in package_list:
                    if not package_name:  # Skip empty package names
                        continue
                        
                    # Determine repo name and version based on package manager
                    if package_manager == 'pip':
                        repo_name = 'pypi'
                        repo_version = ''
                    else:
                        # For system package managers, use the platform as repo name directly
                        # get_all_packages_from_config already handles normalization
                        repo_name = platform
                        repo_version = version or ''
                    
                    # Create PackageInfo object
                    package_info = PackageInfo(
                        name=rosdep_key,           # rosdep key as the logical name
                        bin_name=package_name,     # actual package name in repo
                        repo_name=repo_name,       # repository name
                        repo_version=repo_version, # repository version
                        src_name=package_name,     # source package name
                        version=None,              # package version (not available in rosdep)
                        arch=None,                 # architecture (not specified in rosdep)
                        description=f"Package from rosdep entry '{rosdep_key}'",
                        confidence=1.0,            # full confidence for rosdep entries
                        metadata={
                            'rosdep_key': rosdep_key,
                            'platform': platform,
                            'os_version': version,
                            'package_manager': package_manager,
                            'original_config': pkg_config
                        }
                    )
                    
                    packages.append(package_info)
                    
        except Exception as e:
            # Skip if get_all_packages_from_config fails
            logger.error(f"get_all_packages_from_config failed for {rosdep_key}: {e}, skipping entry")
        
        return DistributionTable(packages)
        
class RosdepDatabase:
    """Enhanced database with easy query methods"""
    def __init__(self, config: Dict = None):
        self._data: Dict[str, Dict] = {}
        if config is None:
            self._config = load_config()
        else:
            self._config = config
        self._distribution_entries = []

    def _is_package_manager(self, key: str) -> bool:
        """Check if a key represents a known package manager"""
        known_package_managers = {
            'apt', 'pip', 'source', 'dnf', 'yum', 'portage', 'brew',
            'pacman', 'apt-cyg', 'pkg_add', 'zypper', 'port','slackware','homebrew','slackpkg','macports'
        }
        return key in known_package_managers

    def load_yaml(self, yaml_contents: str) -> None:
        """Load and process YAML data"""
        try:
            raw_data = yaml.safe_load(yaml_contents)
            if not isinstance(raw_data, dict):
                raise RosdepLookupError("Invalid YAML: not a dictionary")
            self._data.update(self._process_data(raw_data))
        except yaml.YAMLError as e:
            raise RosdepLookupError(f"YAML parsing error: {e}")

    def load_distribution(self, dist_db: 'DistributionDatabase', distro_name: str = None) -> None:
        """Load data from a distribution database"""
        rosdep_data = dist_db.convert_to_rosdep_data(distro_name)
        # Update our database with this data
        self._data.update(rosdep_data)
        self._distribution_entries = list(rosdep_data.keys())
        
    def _process_data(self, raw_data: Dict) -> Dict:
        """Process raw YAML data into standardized format"""
        processed = {}
        for key, value in raw_data.items():
            processed[key] = self._process_entry(value)
        return processed

    def _process_entry(self, entry_data: Any) -> Dict:
        """Process a single rosdep entry"""
        if isinstance(entry_data, str):
            # Simple case: just package name
            return {'*': {'apt': {'packages': [entry_data]}}}
            
        if isinstance(entry_data, list):
            # Handle list of packages
            return {'*': {'apt': {'packages': entry_data}}}
            
        if entry_data is None:
            # Handle null/None value - package not available
            return {}
            
        if not isinstance(entry_data, dict):
            raise RosdepLookupError(f"Invalid entry data type: {type(entry_data)}")
            
        processed = {}
        for platform, platform_data in entry_data.items():
        
            if platform_data is None:
                # Platform explicitly set to null, add with empty dict
                processed[platform] = {'*': {}}
                continue
            elif platform == '*':
                if 'pip' in platform_data.keys():
                    if isinstance(platform_data['pip'], dict):
                        # pip:
                        #    packages: [xx]
                        #    depends: [xx]  # filter out non-essential keys
                        filtered_pip = self._filter_package_manager_data(platform_data['pip'])
                        processed['*'] = {'*' : {'pip': filtered_pip}}
                    else:
                        # pip: [xx]
                        processed['*'] = {'*' : {'pip': {'packages': platform_data['pip']}}}
                continue
            elif isinstance(platform_data, str):
                # Simple case: direct package name
                processed[platform] = {'*': self._get_default_package_manager(platform, [platform_data])}
            elif isinstance(platform_data, list):
                # Handle list of packages
                processed[platform] = {'*': self._get_default_package_manager(platform, platform_data)}
            elif isinstance(platform_data, dict):
                # This could be versioned platform or specific package manager
                processed_platform = self._process_platform_data(platform, platform_data)
                if processed_platform != {}:
                    # Avoid cases like ubuntu: "*": null
                    processed[platform] = processed_platform
            else:
                raise RosdepLookupError(f"Invalid platform data type for {platform}: {type(platform_data)}")
                
        return processed

    def _get_default_package_manager(self, platform: str, packages: List[str]) -> Dict:
        """Get the default package manager configuration for a platform"""
        package_manager_map = {
            'arch': {'pacman': {'packages': packages}},
            'cygwin': {'apt-cyg': {'packages': packages}},
            'debian': {'apt': {'packages': packages}},
            'fedora': {'dnf': {'packages': packages}},
            'freebsd': {'pkg_add': {'packages': packages}},
            'gentoo': {'portage': {'packages': packages}},
            'openembedded': {'opkg': {'packages': packages}},
            'osx': {'brew': {'packages': packages}},  # Supporting both homebrew and macports
            'opensuse': {'zypper': {'packages': packages}},
            'rhel': {'yum': {'packages': packages}},
            'ubuntu': {'apt': {'packages': packages}},
            'macports': {'port': {'packages': packages}}  # Legacy support for macports
        }
        
        return package_manager_map.get(platform, {'apt': {'packages': packages}})  # Default to apt if unknown
    
    def _filter_package_manager_data(self, pkg_data: Any) -> Dict:
        """
        Filter package manager data to keep only essential keys.
        
        Handles various input formats and normalizes them to {packages: [...]} format.
        
        Examples:
        - Input: {packages: [pkg1], depends: [dep1]} -> Output: {packages: [pkg1]}
        - Input: [pkg1, pkg2] -> Output: {packages: [pkg1, pkg2]}
        - Input: {packages: [pkg1]} -> Output: {packages: [pkg1]}
        """
        if not isinstance(pkg_data, dict):
            # Handle direct list or string inputs
            return {'packages': pkg_data}
        
        # Keep only 'packages' key, filter out 'depends' and other non-essential keys
        filtered = {}
        if 'packages' in pkg_data:
            # Standard case: already has packages key
            filtered['packages'] = pkg_data['packages']
        else:
            # Fallback: if no 'packages' key, treat the first value as packages
            # This handles malformed entries
            filtered['packages'] = list(pkg_data.values())[0] if pkg_data else []
        
        return filtered

    def _process_platform_data(self, platform: str, platform_data: Dict) -> Dict:
        """
        Process platform-specific data to normalize it into the standard 4-level structure:
        platform -> version -> package_manager -> {packages: [...]}
        
        This method handles various YAML entry formats and converts them to the canonical structure.
        """
        
        # ========== CASE 1: Direct Package Manager Specification ==========
        # Handles entries like:
        # osx:
        #   macports:
        #     packages: [libdc1394]
        # OR
        # debian:
        #   pip:
        #     packages: [some-package]
        #     depends: [some-dep]  # <- this gets filtered out
        
        has_package_managers = any(self._is_package_manager(key) for key in platform_data.keys())
        
        if has_package_managers:
            # Convert: {macports: {packages: [...]}} -> {'*': {macports: {packages: [...]}}}
            version_data = {}
            for key, value in platform_data.items():
                if self._is_package_manager(key):
                    if isinstance(value, dict):
                        # Filter out non-essential keys like 'depends', keep only 'packages'
                        # Example: {packages: [...], depends: [...]} -> {packages: [...]}
                        filtered_value = self._filter_package_manager_data(value)
                        version_data[key] = filtered_value
                    else:
                        # Handle: macports: [package-name] -> macports: {packages: [package-name]}
                        version_data[key] = {'packages': value}
            
            if version_data:
                return {'*': version_data}
        
        processed = {}
        
        # ========== CASE 2: Default Version Processing ==========
        # Handles entries like:
        # debian:
        #   '*': [package-list]  # <- default version
        # OR
        # debian:
        #   '*':
        #     pip:
        #       packages: [some-package]
        
        if '*' in platform_data:
            default_data = platform_data['*']
            if isinstance(default_data, list):
                # Convert: '*': [pkg1, pkg2] -> '*': {apt: {packages: [pkg1, pkg2]}}
                processed['*'] = self._get_default_package_manager(platform, default_data)
            elif isinstance(default_data, dict):
                # Handle package manager entries at the default version level
                filtered_default = {}
                for pkg_mgr, pkg_data in default_data.items():
                    if self._is_package_manager(pkg_mgr):
                        if isinstance(pkg_data, dict):
                            # Filter out 'depends' and other non-essential keys
                            filtered_default[pkg_mgr] = self._filter_package_manager_data(pkg_data)
                        else:
                            # Convert: pip: [package] -> pip: {packages: [package]}
                            filtered_default[pkg_mgr] = {'packages': pkg_data}
                    else:
                        # Not a package manager, keep as-is (might be version-specific data)
                        filtered_default[pkg_mgr] = pkg_data
                
                if filtered_default:
                    processed['*'] = filtered_default
                else:
                    # Fallback: use default package manager for empty case
                    processed['*'] = self._get_default_package_manager(platform, [])
            elif default_data is None:
                # Handle: ubuntu: "*": null (package not available for default version)
                processed['*'] = {}
            else:
                raise RosdepLookupError(f"Invalid default data type: {type(default_data)}")
        
        # ========== CASE 3: Specific Version Processing ==========
        # Handles entries like:
        # debian:
        #   buster: [package-list]           # -> buster: {apt: {packages: [package-list]}}
        #   bullseye: {}                     # -> bullseye: {} (not available)
        #   bookworm:                        # -> bookworm: {apt: {packages: [...]}}
        #     apt:
        #       packages: [package-name]
        
        for key, value in platform_data.items():
            if key == '*':
                continue  # Already processed above
            
            if value is None:
                # Handle: version_name: null (not available for this version)
                processed[key] = {}
            elif isinstance(value, list):
                # Handle: version_name: [pkg1, pkg2] -> version_name: {apt: {packages: [pkg1, pkg2]}}
                processed[key] = self._get_default_package_manager(platform, value)
            elif isinstance(value, dict):
                # Process dict - contains package managers
                filtered_dict = {}
                for pkg_mgr, pkg_data in value.items():
                    if self._is_package_manager(pkg_mgr):
                        if isinstance(pkg_data, dict):
                            # Handle: apt: {packages: [...], depends: [...]} -> apt: {packages: [...]}
                            filtered_dict[pkg_mgr] = self._filter_package_manager_data(pkg_data)
                        else:
                            # Handle: apt: [package] -> apt: {packages: [package]}
                            filtered_dict[pkg_mgr] = {'packages': pkg_data}
                    else:
                        # Not a package manager, keep as-is
                        filtered_dict[pkg_mgr] = pkg_data
                processed[key] = filtered_dict
            else:
                raise RosdepLookupError(f"Invalid version data type for {key}: {type(value)}")
        
        return processed

    def get_entry(self, rosdep_key: str) -> Optional[RosdepDatabaseEntry]:
        """Get a rosdep entry by key"""
        if rosdep_key not in self._data:
            return None
        return RosdepDatabaseEntry(rosdep_key, self._data[rosdep_key], self._config)

    def get_all_platforms(self) -> List[str]:
        """Get all platforms across all entries"""
        platforms = []
        for data in self._data.values():
            platforms.extend(data.keys())
        return platforms

    def get_entries_for_platform(self, platform: str) -> List[str]:
        """Get all rosdep keys available for a platform"""
        return [key for key, data in self._data.items() if platform in data]
      
    def is_distribution_entry(self, rosdep_key: str) -> bool:
        """Check if a rosdep key is a distribution entry"""
        return rosdep_key in self._distribution_entries
      
    def get_all_entries(self, include_distribution_entries=False) -> List[str]:
        """Get all rosdep keys across all platforms"""
        if include_distribution_entries:
            return list(self._data.keys())
        else:
            return list(set(self._data.keys()) - set(self._distribution_entries))  
          
    def resolve_dependencies(self, rosdep_key: str, platform: str, 
                           version: str = None) -> Optional[Dict]:
        """Resolve dependencies for a specific platform/version"""
        entry = self.get_entry(rosdep_key)
        if not entry:
            return None
        return entry.get_package_details(platform, version)
     
class DistributionPackage:
    """Represents a package from a ROS distribution file"""
    def __init__(self, name: str, data: Dict):
        self.name = name
        self._data = data
        self._packages = self._extract_packages()
        
    def _extract_packages(self) -> List[str]:
        """Extract package names from release section"""
        if 'release' in self._data and 'packages' in self._data['release']:
            return self._data['release']['packages']
        # If packages not specified, the repo name is the package name
        return [self.name]
        
    def get_status(self) -> str:
        """Get the status of the package (maintained, developed, etc.)"""
        return self._data.get('status', 'unknown')
        
    def get_source_url(self) -> Optional[str]:
        """Get source repository URL"""
        if 'source' in self._data and 'url' in self._data['source']:
            return self._data['source']['url']
        return None
        
    def get_source_branch(self) -> Optional[str]:
        """Get source repository branch/version"""
        if 'source' in self._data and 'version' in self._data['source']:
            return self._data['source']['version']
        return None
        
    def get_release_url(self) -> Optional[str]:
        """Get release repository URL"""
        if 'release' in self._data and 'url' in self._data['release']:
            return self._data['release']['url']
        return None
        
    def get_release_version(self) -> Optional[str]:
        """Get release version"""
        if 'release' in self._data and 'version' in self._data['release']:
            return self._data['release']['version']
        return None
        
    def get_packages(self) -> List[str]:
        """Get list of packages in this repository"""
        return self._packages

class DistributionDatabase:
    """Represents a ROS distribution and its packages"""
    def __init__(self, config: Dict = None):
        self._data = {}
        self._release_platforms = {}
        self._metadata = {}
        if config is None:
            self._config = load_config()
        else:
            self._config = config
        
    def load_yaml(self, yaml_contents: str) -> None:
        """Load and process distribution YAML data"""
        try:
            raw_data = yaml.safe_load(yaml_contents)
            if not isinstance(raw_data, dict):
                raise RosdepLookupError("Invalid YAML: not a dictionary")
            
            # Extract release platforms
            if 'release_platforms' in raw_data:
                self._release_platforms = raw_data['release_platforms']
                
            # Extract repositories
            if 'repositories' in raw_data:
                for repo_name, repo_data in raw_data['repositories'].items():
                    self._data[repo_name] = DistributionPackage(repo_name, repo_data)
                    
            # Extract metadata
            for key, value in raw_data.items():
                if key not in ['release_platforms', 'repositories']:
                    self._metadata[key] = value
                    
        except yaml.YAMLError as e:
            raise RosdepLookupError(f"YAML parsing error: {e}")

    def get_distribution_type(self) -> Optional[str]:
        """Get the distribution type (ros1, ros2, etc.)"""
        return self._metadata.get('distribution_type')
        
    def get_distribution_version(self) -> Optional[str]:
        """Get the distribution version (e.g., python version)"""
        return self._metadata.get('python_version')
        
    def get_release_platforms(self) -> Dict:
        """Get the supported platforms for this distribution"""
        return self._release_platforms
        
    def get_package_names(self) -> List[str]:
        """Get all repository names in the distribution"""
        return list(self._data.keys())
        
    def get_all_packages(self) -> List[str]:
        """Get all package names (including sub-packages)"""
        all_packages = []
        for repo in self._data.values():
            all_packages.extend(repo.get_packages())
        return all_packages
        
    def get_packages_by_status(self, status: str) -> List[str]:
        """Get packages with specified status"""
        return [name for name, pkg in self._data.items() if pkg.get_status() == status]
        
    def get_package(self, package_name: str) -> Optional[DistributionPackage]:
        """Get package by name"""
        if package_name in self._data:
            return self._data[package_name]
            
        # Check if the package is a subpackage of a repository
        for repo in self._data.values():
            if package_name in repo.get_packages():
                return repo
                
        return None
        
    def convert_to_rosdep_data(self, distro_name: str = None) -> Dict:
        """
        Convert distribution data to rosdep data format
        This mimics the behavior of get_gbprepo_as_rosdep_data in rosdep
        """
        rosdep_data = {}
        
        # Process each repository
        for repo_name, repo in self._data.items():
            for pkg_name in repo.get_packages():
                
                # Create ROS package name (with underscores converted to dashes)
                # package_name = f'ros-{distro_name}-{pkg_name}'
                
                # Currently don't add distro name 
                package_name = pkg_name.replace('_', '-')
                rosdep_data[package_name] = {}
                # Add OSX/Homebrew entry
                rosdep_data[package_name]['osx'] = {
                    'brew': {'packages': [f'{repo_name}']}
                }
                
                # Add entries for each platform in release_platforms
                for os_name, os_versions in self._release_platforms.items():
                    if os_name not in rosdep_data[package_name]:
                        rosdep_data[package_name][os_name] = {}
                        
                    # Get default package manager for platform
                    default_installer = self._get_default_installer(os_name)
                    # Add entry for each OS version
                    for os_version in os_versions:
                        rosdep_data[package_name][os_name][os_version] = {
                            default_installer: {'packages': [package_name]}
                        }
                
                # Add ROS marker
                rosdep_data[package_name]['_is_ros'] = True
                rosdep_data[package_name]['source_key'] = repo_name
                
        return rosdep_data
    
    def _get_default_installer(self, os_name: str) -> str:
        """Get default installer for an OS"""
        installer_map = {
            'ubuntu': 'apt',
            'debian': 'apt',
            'fedora': 'dnf',
            'rhel': 'yum',
            'opensuse': 'zypper',
            'gentoo': 'portage',
            'arch': 'pacman',
            'osx': 'brew',
            'freebsd': 'pkg_add',
            'cygwin': 'apt-cyg'
        }
        return installer_map.get(os_name, 'apt')
    
    
    
    
    
  
        

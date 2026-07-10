import json
from ..package_info import PackageEntry
from .. import RepositoryCacheCollection, open_compressed_url

def download_nixos_packages(base_url, os_code_name):
    """Download and parse NixOS package database"""
    # Replace $releasever with actual release version
    if os_code_name == '':
        os_code_name = 'unstable'
    
    url = base_url.replace('$releasever', os_code_name)
    print(f'Reading NixOS package metadata from {url}')
    
    try:
        # Use the same caching infrastructure as other repo handlers
        with open_compressed_url(url, timeout=60) as f:
            data = json.load(f)
        
        # The response has format: {"version": 2, "packages": {...}}
        if 'packages' in data:
            return data['packages']
        else:
            return data
        
    except Exception as e:
        print(f"Error downloading NixOS database: {e}")
        return {}


def enumerate_nixos_packages(base_url, os_code_name, os_arch):
    """
    Enumerate packages in a NixOS repository.
    
    :param base_url: the NixOS package database base URL.
    :param os_code_name: the OS version/channel name (e.g. 'unstable', '24.05')
    :param os_arch: the system architecture (ignored for NixOS)
    
    :returns: an enumeration of package entries.
    """
    package_db = download_nixos_packages(base_url, os_code_name)
    
    for pkg_name, pkg_data in package_db.items():
        if not isinstance(pkg_data, dict):
            continue
            
        # Extract package information
        pname = pkg_data.get('pname', pkg_name)
        version = pkg_data.get('version', 'unknown')
        meta = pkg_data.get('meta', {})
        description = meta.get('description', '')
        
        package_url = f"https://search.nixos.org/packages?channel={os_code_name}&query={pkg_name}"
        
        yield PackageEntry(
            name=pkg_name,
            version=version,
            url=package_url,
            source_name=pname,
            binary_name=pkg_name,
            description=description
        )





def nixos_base_url(base_url):
    """
    Create an enumerable cache for a NixOS repository.
    
    :param base_url: the URL of the NixOS package database.
    
    :returns: an enumerable repository cache instance.
    """
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_nixos_packages(base_url, os_code_name, os_arch)
    )
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import io
import os
import re
import tarfile
import zipfile
import hashlib
from rosdep_auditor.package_info import PackageEntry
from rosdep_auditor.tools.file_signature_extractor import get_component_identifier
from rosdep_auditor.tools.cache import save_json_cache, load_json_cache, GLOBAL_CACHE_DIR
from rosdep_auditor.tools import logger
import sys
from typing import List, Set

PYPI_CACHE_DIR = os.path.join(GLOBAL_CACHE_DIR, "pypi_find_package")

cache_dict = {}
# Cache for all packages list (to avoid repeated disk I/O)
_cached_packages_list = None
_full_build_done = False


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _package_entry_to_dict(p: PackageEntry) -> dict:
    return {
        "name": getattr(p, "name", str(p)),
        "version": getattr(p, "version", None),
        "url": getattr(p, "url", None),
        "source_name": getattr(p, "source_name", None),
        "binary_name": getattr(p, "binary_name", None),
        "description": getattr(p, "description", None),
        "filelist": getattr(p, "filelist", None),
    }


def _package_entry_from_dict(d: dict) -> PackageEntry:
    return PackageEntry(
        name=d.get("name"),
        version=d.get("version"),
        url=d.get("url"),
        source_name=d.get("source_name"),
        binary_name=d.get("binary_name"),
        description=d.get("description"),
        filelist=d.get("filelist"),
    )

# Create a session with retry strategy for handling network errors
def _create_session():
    """Create a requests session with retry strategy and timeout."""
    session = requests.Session()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=3,  # Total number of retries
        backoff_factor=1,  # Wait 1, 2, 4 seconds between retries
        status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry on
        allowed_methods=["GET", "HEAD"],  # Only retry on safe methods
        raise_on_status=False  # Don't raise exception on status codes
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

# Global session instance
_session = None

def _get_session():
    """Get or create the global session."""
    global _session
    if _session is None:
        _session = _create_session()
    return _session

def _filter_common_files(filelist: list, return_paths: bool = True) -> list:
    """
    Filter out common files shared by every PyPI package using get_component_identifier.
    
    This applies all the ignore rules from file_signature_extractor including:
    - Metadata files (.dist-info/, .egg-info/, PKG-INFO, etc.)
    - Build files (setup.py, setup.cfg, MANIFEST.in, pyproject.toml)
    - Documentation files (.txt, .md, .rst, .html, etc.)
    - Media files (.png, .jpg, etc.)
    - Common low-value files (LICENSE, COPYING, README, __init__.py, etc.)
    - And other patterns defined in file_signature_extractor
    """
    if not filelist:
        return filelist
    
    filtered = []
    for fname in filelist:
        # Convert relative path to absolute-like path for get_component_identifier
        # (it expects paths starting with /)
        path = fname if fname.startswith('/') else f'/{fname}'
        
        result = get_component_identifier(path, return_original=return_paths)
        if result is not None:
            filtered.append(result)
    
    return filtered

def _get_filelist_for_package(pkg: str, version: str, data: dict, return_paths: bool = True):
    """
    Extract file list from PyPI package distribution.
    
    Tries to get wheel (bdist_wheel) first, then falls back to sdist.
    For wheels: uses RECORD file if available, otherwise zip namelist.
    For sdists: uses SOURCES.txt if available, otherwise tar members.
    
    Returns tuple of (filelist, filelist_source) or (None, None) on error.
    """
    try:
        files = data["releases"].get(version, [])
        if not files:
            return None, None

        # Prefer wheel over sdist
        pick = next((f for f in files if f["packagetype"] == "bdist_wheel"), None) or \
               next((f for f in files if f["packagetype"] == "sdist"), None)
        if not pick:
            return None, None

        session = _get_session()
        try:
            blob = session.get(pick["url"], timeout=(10, 30)).content  # (connect timeout, read timeout)
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout, requests.exceptions.RequestException):
            # Return None to indicate failure (caller decides what to do).
            return None, None
        time.sleep(0.2)  # Rate limiting

        # Handle wheel
        if pick["packagetype"] == "bdist_wheel":
            z = zipfile.ZipFile(io.BytesIO(blob))
            # Zip archives can contain explicit directory entries ending with '/'.
            # Keep only non-empty, non-directory entries.
            names = [n for n in z.namelist() if n and not n.endswith("/")]

            meta = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
            if not meta:
                return [], "no_METADATA"

            rec = next((n for n in names if n.endswith(".dist-info/RECORD")), None)
            if rec:
                filelist = [ln.split(",", 1)[0] for ln in z.read(rec).decode("utf-8", "replace").splitlines() if ln]
                filelist = _filter_common_files(filelist, return_paths=return_paths)
                return filelist, "wheel:RECORD"
            filelist = _filter_common_files(names, return_paths=return_paths)
            return filelist, "wheel:zip_namelist"

        # Handle sdist
        t = tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")
        members = t.getmembers()
        # Tar archives can contain directory entries (sometimes without trailing '/').
        # Keep only non-directory entries.
        names = [m.name for m in members if m.name and not m.isdir()]

        # Check for PKG-INFO as metadata indicator
        pkg_info = next((n for n in names if n.endswith("/PKG-INFO") or n == "PKG-INFO"), None)
        if not pkg_info:
            return [], "no_METADATA"

        src = next((m for m in members if re.search(r"\.egg-info/SOURCES\.txt$", m.name)), None)
        if src:
            txt = t.extractfile(src).read().decode("utf-8", "replace")
            filelist = [ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")]
            filelist = _filter_common_files(filelist, return_paths=return_paths)
            return filelist, "sdist:SOURCES.txt"

        filelist = _filter_common_files(names, return_paths=return_paths)
        return filelist, "sdist:tar_members"
    except Exception:
        # Return None on any error (network, parsing, etc.)
        return None, None

def find_package_in_pypi(package_name: str, return_paths: bool = True):
    """Search for package in PyPI using their JSON API
    
    Args:
        package_name: Name of the package to search for
        
    Returns:
        PackageEntry if found, None otherwise
        
    Raises:
        requests.exceptions.RequestException: If network request fails after retries
    """
    norm_name = (package_name or "").strip()
    cache_key = (norm_name.lower(), return_paths)
    if cache_key in cache_dict:
        return cache_dict[cache_key]

    url = f"https://pypi.org/pypi/{norm_name}/json"
    # Cache key must include return_paths to avoid mixing modes.
    path_mode = "fullpath" if return_paths else "ids"
    url_key_prefix = _md5(url.lower())[:16]
    persistent_key = f"{url_key_prefix}_{path_mode}"

    cached = load_json_cache("pypi_find_package", persistent_key, subdir="pypi_find_package")
    if cached is not None:
        try:
            entry = _package_entry_from_dict(cached)
            cache_dict[cache_key] = entry
            return entry
        except Exception:
            # Corrupt cache; fall through to network fetch.
            pass
    else:
        # Fallback: if we're requesting component-ids but only a "fullpath" cache exists,
        # load it and convert its filelist locally (no network).
        if not return_paths:
            persistent_key_fullpath = f"{url_key_prefix}_fullpath"
            cached_fullpath = load_json_cache(
                "pypi_find_package",
                persistent_key_fullpath,
                subdir="pypi_find_package",
            )
            if cached_fullpath is not None:
                try:
                    filelist = cached_fullpath.get("filelist")
                    if filelist:
                        cached_fullpath["filelist"] = _filter_common_files(filelist, return_paths=False)
                        logger.debug(f"Filtered filelist using return_paths=True cache: {cached_fullpath['filelist']}")
                    entry = _package_entry_from_dict(cached_fullpath)
                    cache_dict[cache_key] = entry
                    save_json_cache(
                        "pypi_find_package",
                        persistent_key,
                        cached_fullpath,
                        subdir="pypi_find_package",
                    )
                    return entry
                except Exception:
                    # If fallback cache is corrupt, proceed to network fetch.
                    pass

    session = _get_session()
    
    try:
        # Use session with retry strategy and timeout
        response = session.get(url, timeout=(10, 30))  # (connect timeout, read timeout)
        time.sleep(1)  # Rate limiting
        
        if response.status_code == 200:
            data = response.json()
            latest_version = data['info']['version']
            url = data['info']['package_url']
            canonical_name = data['info'].get('name') or package_name
            
            # Get description from API - it could be in 'summary' or 'description' fields
            description = data['info'].get('summary', '')
            if not description and data['info'].get('description'):
                # If summary is empty but description exists, use first line of description
                description = data['info']['description'].split('\n')[0]

            # Extract file list
            filelist, filelist_source = _get_filelist_for_package(package_name, latest_version, data, return_paths=return_paths)
            entry = PackageEntry(
                name=canonical_name,
                version=latest_version,
                url=url,
                source_name=canonical_name,
                binary_name=canonical_name,
                description=description,
                filelist=filelist
            )
            cache_dict[cache_key] = entry
            save_json_cache("pypi_find_package", persistent_key, _package_entry_to_dict(entry), subdir="pypi_find_package")
            return entry
        else:
            return None
    except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
            requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
        # Re-raise the exception so the caller can handle it appropriately
        # This allows the error to be logged at the calling site with proper context
        raise


def build_full_cache(package_list: List[str], return_paths: bool = True) -> int:
    """
    Build cache for a list of PyPI packages.

    This function fetches package information from PyPI API and caches it locally.
    Useful for pre-populating cache before batch searches.

    Args:
        package_list: List of package names to cache
        return_paths: Whether to use full paths or component IDs in filelist

    Returns:
        Number of successfully cached packages
    """
    cached_count = 0
    for pkg_name in package_list:
        try:
            result = find_package_in_pypi(pkg_name, return_paths=return_paths)
            if result:
                cached_count += 1
        except Exception as e:
            logger.warning(f"Failed to cache package {pkg_name}: {e}")

    return cached_count


def get_all_cached_packages(force_refresh: bool = False, ground_truth_yaml: str = None) -> List[PackageEntry]:
    """
    Get all cached PyPI packages from disk cache.

    This function checks the PYPI_FULL_BUILD environment variable:
    - If 'true': Calls build_full_cache() with top PyPI packages first (only once per session)
    - If 'false' or unset: Only loads existing cache files

    The result is cached in memory to avoid repeated disk I/O.
    Use force_refresh=True to reload from disk.

    Args:
        force_refresh: If True, reload from disk even if cached in memory

    Returns:
        List of PackageEntry objects from cache
    """
    global _cached_packages_list, _full_build_done

    # Return cached result if available
    if _cached_packages_list is not None and not force_refresh:
        return _cached_packages_list

    # Check if we should do a full cache build (only once per session)
    full_build = os.getenv('PYPI_FULL_BUILD', 'false').lower() == 'true'

    if full_build and not _full_build_done:
        # Build cache for popular packages (this can be customized)
        # For full PyPI cache, you'd need to fetch the complete package list from PyPI
        popular_packages = []
        logger.info(f"PYPI_FULL_BUILD=true: Building cache for {len(popular_packages)} packages...")
        build_full_cache(popular_packages)
        _full_build_done = True

    # Load from disk cache
    if not os.path.exists(PYPI_CACHE_DIR):
        _cached_packages_list = []
        return []

    packages = []
    cache_files = [f for f in os.listdir(PYPI_CACHE_DIR) if f.endswith('.json')]

    for cache_file in cache_files:
        # Extract cache key from filename: pypi_find_package_<key>.json
        cache_key = cache_file.replace('pypi_find_package_', '').replace('.json', '')

        # Try loading both modes (fullpath and ids)
        for mode in ['fullpath', 'ids']:
            full_key = f"{cache_key.rsplit('_', 1)[0]}_{mode}"
            cached = load_json_cache("pypi_find_package", full_key, subdir="pypi_find_package")
            if cached:
                try:
                    entry = _package_entry_from_dict(cached)
                    # Avoid duplicates
                    if entry not in packages:
                        packages.append(entry)
                    break
                except Exception:
                    pass

    _cached_packages_list = packages
    return packages


def find_packages_by_description(description: str) -> Set[PackageEntry]:
    """
    Find PyPI packages by searching description in cached packages.

    Args:
        description: Description string to search for

    Returns:
        Set of PackageEntry objects whose description contains the search string
    """
    if not description:
        return set()

    packages = get_all_cached_packages()
    results = set()

    description_lower = description.lower().strip()
    for pkg in packages:
        pkg_desc = pkg.description
        if isinstance(pkg_desc, list):
            pkg_desc = ' '.join(pkg_desc)
        if pkg_desc and description_lower in pkg_desc.lower().strip():
            results.add(pkg)

    return results


def find_packages_by_filelist(file_list: List[str]) -> Set[PackageEntry]:
    """
    Find PyPI packages by filelist in cached packages.

    Args:
        file_list: List of files to search for

    Returns:
        Set of PackageEntry objects whose filelist intersects with the input
    """
    if not file_list:
        return set()

    packages = get_all_cached_packages()
    results = set()

    # Convert to set for O(1) intersection
    file_list_set = set(file_list) if not isinstance(file_list, set) else file_list

    for pkg in packages:
        if pkg.filelist and file_list_set.intersection(pkg.filelist):
            results.add(pkg)

    return results

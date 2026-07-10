import os
import re
import hashlib
from typing import Iterable, List, Optional
import xml.etree.ElementTree as ET
import requests
from ..package_info import PackageEntry
from .. import RepositoryCacheCollection
from ..tools.cache import save_json_cache, load_json_cache


def _read_text(url: str) -> str:
    """Fetch text content via HTTP using requests (no compression helper)."""
    resp = requests.get(url, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    # Rely on requests' encoding detection; fallback to utf-8
    resp.encoding = resp.encoding or 'utf-8'
    return resp.text


def _list_categories(base_url: str) -> List[str]:
    """List Gentoo categories from profiles/categories."""
    categories_url = os.path.join(base_url, 'profiles', 'categories')
    print('Reading Gentoo categories from ' + categories_url)
    text = _read_text(categories_url)
    categories: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        categories.append(line)
    return categories


_HREF_RE = re.compile(r'href="([^"#?]+)"', re.IGNORECASE)


def _list_directory_entries(url: str) -> List[str]:
    """Return names from a simple HTTP directory listing."""
    html = _read_text(url)
    entries: List[str] = []
    for href in _HREF_RE.findall(html):
        # Only consider relative names
        if href.startswith('/'):
            continue
        # Skip parent links
        if href in ('../', './'):
            continue
        entries.append(href)
    return entries


def _list_packages_in_category(base_url: str, category: str) -> Iterable[str]:
    """List package directory names within a category using HTTP index."""
    category_url = os.path.join(base_url, category, '')
    print('Reading Gentoo packages from ' + category_url)
    for entry in _list_directory_entries(category_url):
        # We only care about subdirectories (package names)
        if not entry.endswith('/'):
            continue
        pkg_name = entry[:-1]
        # Basic sanity check on package names
        if re.fullmatch(r'[a-z0-9][a-z0-9+._-]*', pkg_name):
            yield pkg_name


def _natural_version_key(version: str):
    parts = re.split(r'(\d+)', version)
    key = []
    for part in parts:
        if part.isdigit():
            try:
                key.append(int(part))
            except Exception:
                key.append(part)
        else:
            key.append(part)
    return tuple(key)


def _pick_latest_version_from_ebuilds(package_dir_entries: List[str], pkg_name: str) -> Optional[str]:
    versions: List[str] = []
    prefix = pkg_name + '-'
    for entry in package_dir_entries:
        if not entry.endswith('.ebuild'):
            continue
        fname = entry
        if '/' in fname:
            fname = fname.split('/')[-1]
        if not fname.startswith(prefix) or not fname.endswith('.ebuild'):
            continue
        ver = fname[len(prefix):-7]
        if ver:
            versions.append(ver)
    if not versions:
        return None
    versions.sort(key=_natural_version_key, reverse=True)
    return versions[0]


def _read_package_description(package_dir_url: str) -> Optional[str]:
    # metadata.xml is optional; try to read and parse <longdescription> or <description>
    try:
        xml_url = os.path.join(package_dir_url, 'metadata.xml')
        xml_text = _read_text(xml_url)
        root = ET.fromstring(xml_text)
        # Try longdescription, then description (normalize to one line)
        long_desc = root.find('.//longdescription')
        if long_desc is not None and (long_desc.text or '').strip():
            return ' '.join(long_desc.text.split())
        desc = root.find('.//description')
        if desc is not None and (desc.text or '').strip():
            return ' '.join(desc.text.split())
    except Exception:
        # Failed to read metadata.xml - description will be None
        pass
    return None


def find_package_in_gentoo_from_base(base_url: str, package_name: str) -> Optional[PackageEntry]:
    """Lookup a single Gentoo package directly from the configured mirror.

    - Scans categories from profiles/categories
    - Checks for a matching package directory under each category
    - Picks latest version from .ebuild filenames
    - Reads metadata.xml for description when available
    """
    try:
        for category in _list_categories(base_url):
            category_url = os.path.join(base_url, category, '')
            entries = _list_directory_entries(category_url)
            if f'{package_name}/' not in entries:
                continue
            # Found category
            package_dir_url = os.path.join(category_url, package_name, '')
            pkg_entries = _list_directory_entries(package_dir_url)
            version = _pick_latest_version_from_ebuilds(pkg_entries, package_name) or 'unknown'
            description = _read_package_description(package_dir_url)
            full_name = f'{category}/{package_name}'
            pkg_url = f'https://packages.gentoo.org/packages/{full_name}'
            return PackageEntry(
                name=full_name,
                version=version,
                url=pkg_url,
                source_name=full_name,
                binary_name=full_name,
                description=description,
            )
    except Exception:
        return None
    return None


def _extract_base_url_from_source(source) -> Optional[str]:
    # RepositoryCacheCollection holds an _iterator lambda that closes over base_url
    try:
        iterator_fn = getattr(source, '_iterator', None)
        if iterator_fn is None:
            return None
        closure = getattr(iterator_fn, '__closure__', None)
        if not closure:
            return None
        for cell in closure:
            val = cell.cell_contents
            if isinstance(val, str) and val.startswith('http'):
                return val
    except Exception:
        return None
    return None


def find_package_in_gentoo_from_sources(package_name: str, sources: List[object]) -> Optional[PackageEntry]:
    for source in sources:
        base_url = _extract_base_url_from_source(source)
        if not base_url:
            continue
        result = find_package_in_gentoo_from_base(base_url, package_name)
        if result:
            return result
    return None


def enumerate_gentoo_packages(base_url: str, os_code_name: str, os_arch: str):
    """Enumerate packages in a Gentoo Portage repository by directory listing.

    This enumerates package names (without enumerating versions) to keep
    network requests minimal. Presence detection for rosdep only requires
    the name; version may be unknown.
    """
    try:
        for category in _list_categories(base_url):
            for pkg_name in _list_packages_in_category(base_url, category):
                full_name = f'{category}/{pkg_name}'
                package_url = f'https://packages.gentoo.org/packages/{full_name}'
                yield PackageEntry(
                    name=pkg_name,
                    version='unknown',
                    url=package_url,
                    source_name=full_name,
                    binary_name=pkg_name,
                    description=None,
                )
    except Exception as e:
        # Fail fast but loud; better than yielding bogus curated data
        print(f'Error enumerating Gentoo repository: {e}')
        return


def gentoo_base_url(base_url: str):
    """Create an enumerable cache for a Gentoo Portage repository."""
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch: enumerate_gentoo_packages(base_url, os_code_name, os_arch)
    )


# On-demand Gentoo lookup similar to PyPI's approach
# In-memory cache
_cache_dict: dict = {}


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _package_entry_to_dict(p: PackageEntry) -> dict:
    return {
        "name": getattr(p, "name", None),
        "version": getattr(p, "version", None),
        "url": getattr(p, "url", None),
        "source_name": getattr(p, "source_name", None),
        "binary_name": getattr(p, "binary_name", None),
        "description": getattr(p, "description", None),
    }


def _package_entry_from_dict(d: dict) -> PackageEntry:
    return PackageEntry(
        name=d.get("name"),
        version=d.get("version"),
        url=d.get("url"),
        source_name=d.get("source_name"),
        binary_name=d.get("binary_name"),
        description=d.get("description"),
    )


def _find_on_packages_site(package_name: str) -> Optional[PackageEntry]:
    try:
        import requests
    except Exception:
        return None

    search_url = f'https://packages.gentoo.org/packages/search?q={package_name}'
    try:
        resp = requests.get(search_url, timeout=20)
        if resp.status_code != 200:
            return None
        html = resp.text
        # Look for exact match links like /packages/<category>/<name>
        # Prefer exact name match at end of href
        candidates = re.findall(r'href="/packages/([a-z0-9+_.-]+)/([a-z0-9+_.-]+)"', html, re.IGNORECASE)
        best = None
        for cat, name in candidates:
            if name == package_name:
                best = (cat, name)
                break
        if not best and candidates:
            best = candidates[0]
        if not best:
            return None
        category, name = best
        full_name = f'{category}/{name}'
        pkg_url = f'https://packages.gentoo.org/packages/{full_name}'
        return PackageEntry(
            name=name,
            version='unknown',
            url=pkg_url,
            source_name=full_name,
            binary_name=name,
            description=None,
        )
    except Exception:
        return None


def find_package_in_gentoo(package_name: str) -> Optional[PackageEntry]:
    """Find a Gentoo package by category/name directly from the steadfast mirror.

    Example: package_name='dev-python/pymongo' -> checks
    https://mirror.steadfast.net/gentoo-portage/dev-python/pymongo/
    Returns PackageEntry on success; None if not found.
    """
    # Check in-memory cache
    if package_name in _cache_dict:
        return _cache_dict[package_name]

    # Check persistent cache
    cache_key = _md5(package_name.lower())[:16]
    cached = load_json_cache("gentoo_find_package", cache_key, subdir="gentoo_find_package")
    if cached is not None:
        try:
            entry = _package_entry_from_dict(cached)
            _cache_dict[package_name] = entry
            return entry
        except Exception:
            # Corrupt cache; fall through to network fetch
            pass

    if '/' not in package_name:
        return None

    try:
        category, name = package_name.split('/', 1)
        base_url = 'https://mirror.steadfast.net/gentoo-portage/'
        pkg_dir_url = os.path.join(base_url, category, name, '')
        # Confirm directory exists by fetching the index page; some packages may
        # not have metadata.xml, so don't rely on it for existence.
        try:
            _read_text(pkg_dir_url)
        except Exception:
            return None
        description = _read_package_description(pkg_dir_url)
        full_name = f'{category}/{name}'
        pkg_url = f'https://packages.gentoo.org/packages/{full_name}'
        entry = PackageEntry(
            name=name,
            version='unknown',
            url=pkg_url,
            source_name=full_name,
            binary_name=full_name,
            description=description,
        )
        # Save to both caches
        _cache_dict[package_name] = entry
        save_json_cache("gentoo_find_package", cache_key,
                       _package_entry_to_dict(entry), subdir="gentoo_find_package")
        return entry
    except Exception:
        return None

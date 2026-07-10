# Copyright (c) 2021, Open Source Robotics Foundation
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Willow Garage, Inc. nor the names of its
#       contributors may be used to endorse or promote products derived from
#       this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from gzip import GzipFile
from lzma import LZMAFile
import hashlib
import json
import os
import socket
import sys
import time
# Avoid importing requests at module import time to prevent environment issues
try:
    import requests  # noqa: F401
except Exception:
    requests = None  # type: ignore
try:
    from urllib.error import HTTPError
    from urllib.error import URLError
    from urllib.request import Request
    from urllib.request import urlopen
except ImportError:
    from urllib2 import HTTPError
    from urllib2 import Request
    from urllib2 import URLError
    from urllib2 import urlopen
import shutil
from pathlib import Path

try:
    from zstandard import ZstdDecompressor
except ImportError:
    ZstdDecompressor = None
try:
    import brotli
except ImportError:
    brotli = None


from .repos.pypi import find_package_in_pypi

from .package_info import PackageEntry
from .tools.cache import GLOBAL_CACHE_DIR

# Configure cache settings
DEFAULT_CACHE_DIR = os.path.join(GLOBAL_CACHE_DIR, 'indexes')


def fmt_os(os_name, os_code_name):
    return (os_name + ' ' + os_code_name) if os_code_name else os_name


def is_probably_gzip(response):
    """
    Determine if a urllib response is likely gzip'd.

    :param response: the urllib response
    """
    return (response.url.endswith('.gz') or
            response.getheader('Content-Encoding') == 'gzip' or
            response.getheader('Content-Type') == 'application/x-gzip')


def is_probably_lzma(response):
    """
    Determine if a urllib response is likely lzma'd.

    :param response: the urllib response
    """
    return (response.url.endswith('.xz') or
            response.getheader('Content-Encoding') == 'xz' or
            response.getheader('Content-Type') == 'application/x-xz')


def is_probably_zstd(response):
    """
    Determine if a urllib response is likely ztsd'd.

    :param response: the urllib response
    """
    return (response.url.endswith('.zst') or
            response.url.endswith('.zck') or
            response.getheader('Content-Encoding') == 'zstd' or
            response.getheader('Content-Type') == 'application/zstd')


def is_probably_brotli(response):
    """
    Determine if a urllib response is likely brotli-compressed.

    :param response: the urllib response
    """
    return (response.url.endswith('.br') or
            response.getheader('Content-Encoding') == 'br' or
            response.getheader('Content-Type') == 'application/brotli')


def get_url_hash(url):
    """
    Generate a hash of the URL to use as a filename.

    :param url: URL to hash
    :returns: A string hash of the URL
    """
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def get_cache_path(url, cache_dir=None):
    """
    Get the path to the cached file for a URL.

    :param url: URL that was downloaded
    :param cache_dir: Directory to store cached files (defaults to DEFAULT_CACHE_DIR)
    :returns: Path to the cached file
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    # Ensure cache directory exists
    os.makedirs(cache_dir, exist_ok=True)

    file_hash = get_url_hash(url)
    return os.path.join(cache_dir, file_hash)


def get_cache_meta_path(url, cache_dir=None):
    """
    Get the path to the cache metadata file for a URL.

    :param url: URL that was downloaded
    :param cache_dir: Directory to store cached files (defaults to DEFAULT_CACHE_DIR)
    :returns: Path to the cache metadata file
    """
    return get_cache_path(url, cache_dir) + '.meta'


def save_to_cache(url, response, cache_dir=None):
    """
    Save a downloaded file to the cache.

    :param url: URL that was downloaded
    :param response: Response object from urlopen
    :param cache_dir: Directory to store cached files (defaults to DEFAULT_CACHE_DIR)
    :returns: Tuple of (cache_file_path, response_type)
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

    cache_file = get_cache_path(url, cache_dir)
    meta_file = get_cache_meta_path(url, cache_dir)

    # Determine compression type
    if is_probably_gzip(response):
        response_type = 'gzip'
    elif is_probably_lzma(response):
        response_type = 'lzma'
    elif is_probably_zstd(response):
        response_type = 'zstd'
    elif is_probably_brotli(response):
        response_type = 'brotli'
    else:
        response_type = 'plain'

    # Save metadata (just store compression type)
    metadata = {
        'type': response_type
    }

    with open(meta_file, 'w') as f:
        json.dump(metadata, f)

    # Save content
    with open(cache_file, 'wb') as f:
        shutil.copyfileobj(response, f)

    return cache_file, response_type


def is_cached(url, cache_dir=None):
    """
    Check if a file is already cached.

    :param url: URL to check
    :param cache_dir: Directory to check for cached files (defaults to DEFAULT_CACHE_DIR)
    :returns: Tuple of (is_cached, metadata) if cached, otherwise (False, None)
    """
    meta_file = get_cache_meta_path(url, cache_dir)
    cache_file = get_cache_path(url, cache_dir)

    # Simply check if both files exist
    if not os.path.exists(meta_file) or not os.path.exists(cache_file):
        return False, None

    try:
        with open(meta_file, 'r') as f:
            metadata = json.load(f)
        return True, metadata
    except (json.JSONDecodeError, IOError):
        return False, None


def open_cached_file(url, cache_dir=None):
    """
    Open a cached file with appropriate decompression.

    :param url: URL of the cached file
    :param cache_dir: Cache directory (defaults to DEFAULT_CACHE_DIR)
    :returns: File-like object with the decompressed content
    """
    is_valid, metadata = is_cached(url, cache_dir)
    if not is_valid:
        return None

    cache_file = get_cache_path(url, cache_dir)

    try:
        f = open(cache_file, 'rb')
        if metadata['type'] == 'gzip':
            return GzipFile(fileobj=f, mode='rb')
        elif metadata['type'] == 'lzma':
            return LZMAFile(f, mode='rb')
        elif metadata['type'] == 'zstd':
            if ZstdDecompressor is None:
                raise ImportError("zstandard module not available for zstd decompression")
            dctx = ZstdDecompressor()
            return dctx.stream_reader(f)
        elif metadata['type'] == 'brotli':
            if brotli is None:
                raise ImportError("brotli module not available for brotli decompression")
            # Decompress entire content into memory for simplicity
            import io
            data = f.read()
            f.close()
            decompressed = brotli.decompress(data)
            return io.BytesIO(decompressed)
        return f
    except IOError:
        if f:
            f.close()
        return None


def open_gz_url(url, retry=2, retry_period=1, timeout=10, cache_dir=None, use_cache=True):
    return open_compressed_url(url, retry, retry_period, timeout, cache_dir, use_cache)


def open_compressed_url(url, retry=2, retry_period=1, timeout=100, cache_dir=None, use_cache=True):
    """
    Open a URL to a possibly compressed file, with caching support.

    :param url: URL to the file.
    :param retry: number of times to re-attempt the download.
    :param retry_period: number of seconds to wait between retry attempts.
    :param timeout: number of seconds to wait for the remote host to respond.
    :param cache_dir: Directory to store cached files (defaults to DEFAULT_CACHE_DIR)

    :returns: file-like object for streaming file data.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    if use_cache:
      # Check if already cached
      cached_file = open_cached_file(url, cache_dir)
      if cached_file:
          return cached_file
    # Not cached, download fresh copy
    request = Request(url, headers={'Accept-Encoding': 'gzip, br',
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
    try:
        f = urlopen(request, timeout=timeout)
    except HTTPError as e:
        if e.code == 503 and retry:
            time.sleep(retry_period)
            return open_gz_url(
                url, retry=retry - 1, retry_period=retry_period,
                timeout=timeout, cache_dir=cache_dir, use_cache=use_cache)
        e.msg += ' (%s)' % url
        raise
    except URLError as e:
        if isinstance(e.reason, socket.timeout) and retry:
            time.sleep(retry_period)
            return open_gz_url(
                url, retry=retry - 1, retry_period=retry_period,
                timeout=timeout, cache_dir=cache_dir, use_cache=use_cache)
        raise URLError(str(e) + ' (%s)' % url)

    if use_cache:
        # Save to cache
        save_to_cache(url, f, cache_dir)
        # Reopen the cached file
        return open_cached_file(url, cache_dir)
    else:
        return f




class RepositoryCache:
    """
    A cache of packages in a repository.

    This class acts as a cache and abstraction layer for the underlying
    platform-specific package enumeration function. It exposes progressive
    methods for testing if a package is present and also enumeration that
    can be performed multiple times without querying the source multiple
    times.
    """

    def __init__(self, iterator):
        self._cache = set()
        self._source_iterator = iterator

    def __iter__(self):
        return self._enumerate_packages()

    def __contains__(self, needle):
        if needle in self._cache:
            return True
        for pkg in self._enumerate_from_source():
            if pkg == needle:
                return True

        return False

    def _enumerate_from_source(self):
        """
        Enumerate packages directly from the source function.

        When the source has no more packages to yield, this function will also
        no longer yield any packages. As this function yields packages, they
        are added to the cache.
        """
        while self._source_iterator:
            try:
                val = next(self._source_iterator)
                self._cache.add(val)
                yield val
            except StopIteration:
                self._source_iterator = None

    def _enumerate_packages(self):
        """
        Enumerate all of the packages in the repository.

        Begin by enumerating any previously enumerated and cached packages, then
        attempt to enumerate any addition packages directly from the source.
        """
        yield from self._cache
        yield from self._enumerate_from_source()


class RepositoryCacheCollection:
    """
    A collection of individual repository caches.

    This class represents a collection of individual repositories for each
    OS, version, and arch, which are all associated with the same basic URL.
    It will create repository caches as necessary to meet enumeration
    requests, and will maintain the caches until the instance is deleted.
    """

    def __init__(self, iterator):
        self._cache = {}
        self._iterator = iterator

    def enumerate_packages(self, os_name, os_code_name, os_arch):
        """
        Enumerate packages in this repository collection for the given platform.

        :param os_name: the name of the OS associated with the packages.
        :param os_code_name: the OS version associated with the packages.
        :param os_arch: the system architecture associated with the packages.

        :returns: An enumerable cache of the packages.
        """
        cache = self._cache.get((os_name, os_code_name, os_arch))
        if not cache:
            cache = RepositoryCache(self._iterator(os_name, os_code_name, os_arch))
            self._cache[(os_name, os_code_name, os_arch)] = cache
        return cache


def summarize_broken_packages(broken):
    """
    Create human-readable summary regarding missing packages.

    :param broken: tuples with information about the broken packages.

    :returns: the human-readable summary.
    """
    # Group and sort by os, version, arch, key
    grouped = {}

    for os_name, os_ver, os_arch, key, package, _ in broken:
        platform = '%s on %s' % (fmt_os(os_name, os_ver), os_arch)
        if platform not in grouped:
            grouped[platform] = set()
        grouped[platform].add('- Package %s for rosdep key %s' % (package, key))

    return '\n\n'.join(
        '* The following %d packages were not found for %s:\n%s' % (
            len(pkg_msgs), platform, '\n'.join(sorted(pkg_msgs)))
        for platform, pkg_msgs in sorted(grouped.items()))

def get_ros_package_name(pkg_name):
    """
    Get the ROS package name from the package name.
    """
    if  pkg_name.startswith('ros-'):
        pkg_name = pkg_name.split('-', 2)
        assert len(pkg_name) == 3, f"Invalid ROS package name: {pkg_name}"
        return pkg_name[2]
    return pkg_name

def _find_package(config, pkg_name, os_name, os_code_name, os_arch):
    """
    Find a package by name for the given platform.

    :param config: the parsed YAML configuration.
    :param pkg_name: the name of the package to be found.
    :param os_name: the name of the OS associated with the package.
    :param os_code_name: the OS version associated with the package.
    :param os_arch: the system architecture associated with the package.

    :returns: the parsed package entry, or None if no package was found.
    """
    use_fullpath = os.getenv('ROSDEP_USE_FULLPATH', 'false').lower() == 'true'

    if os_name not in config['package_sources']:
        if os_name == 'pypi':
            return find_package_in_pypi(pkg_name, return_paths=use_fullpath)
        elif os_name == 'gentoo':
            from .repos.gentoo import find_package_in_gentoo  # type: ignore
            return find_package_in_gentoo(pkg_name)
        return

    if os_name == 'ros':
        pkg_name = get_ros_package_name(pkg_name)

    for os_sources in config['package_sources'][os_name]:
        if isinstance(os_sources, dict):
            sources = os_sources.get(os_code_name, [])
        else:
            sources = [os_sources]
        if not sources:
            print(
                'WARNING: No sources for %s' % (fmt_os(os_name, os_code_name)),
                 file=sys.stderr)
        for source in sources:
            for p in source.enumerate_packages(os_name, os_code_name, os_arch):
                if os_name == 'ros':
                    # ROS packages are named like ros-<version>-<package-name>
                    if get_ros_package_name(p.name) == pkg_name:
                        return p
                else:
                    if os_name == 'nixos':
                      if (pkg_name.lower().strip('-').startswith('python') and
                              p.name.lower().strip('-').startswith('python')):
                        stripped_pkg_name = pkg_name.split('.', 1)[1] if '.' in pkg_name else pkg_name
                        stripped_p_name = p.name.split('.', 1)[1] if '.' in p.name else p.name
                        if stripped_pkg_name == stripped_p_name:
                            return p
                    elif os_name == 'openembedded':
                        if (pkg_name.lower().strip('-').startswith('python') and
                                p.name.lower().strip('-').startswith('python')):
                          stripped_pkg_name = pkg_name.split('@', 1)[0] if '@' in pkg_name else pkg_name
                          stripped_p_name = p.name.split('@', 1)[0] if '@' in p.name else p.name
                          if stripped_pkg_name == stripped_p_name:
                            return p
                    if p.name.lower().strip('-') == pkg_name.lower().strip('-'):
                        # Lazy load filelist for Alpine packages
                        # Follow global return_paths mode (ROSDEP_USE_FULLPATH=true -> full paths)
                        if os_name == 'alpine' and p.filelist is None and hasattr(p, 'url') and p.url:
                            from .repos.apk import _extract_apk_filelist
                            p.filelist = _extract_apk_filelist(p.url, return_paths=use_fullpath)
                        return p


def get_package_link(config, pkg, os_name, os_code_name, os_arch):
    """
    Get an informational link about a package.

    This function uses the package_dashboards configuration to attempt to create
    a URL to an information page regarding a package. If it is unsuccessful, the
    URL to the package itself is returned.

    :param config: the parsed YAML configuration.
    :param pkg: the parsed package entry.
    :param os_name: the name of the OS associated with the package.
    :param os_code_name: the OS version associated with the package.
    :param os_arch: the system architecture associated with the package.

    :returns: a URL to a dashboard or package file.
    """
    for dashboard in config.get('package_dashboards', ()):
        match = dashboard['pattern'].match(pkg.url)
        if match:
            return match.expand(dashboard['url']).format_map({
                'binary_name': pkg.binary_name,
                'name': pkg.name,
                'os_arch': os_arch,
                'os_code_name': os_code_name,
                'os_name': os_name,
                'source_name': pkg.source_name,
                'url': pkg.url,
                'version': pkg.version,
            })

    # No configured dashboard - fall back to package URL
    return pkg.url

def _find_packages_with_filelist(config, file_list, os_name, os_code_name, os_arch):
    """
    Find packages by filelist for the given platform.
    """
    packages = set()
    if os_name not in config['package_sources']:
        return set()
    file_list_set = set(file_list) if not isinstance(file_list, set) else file_list
    for os_sources in config['package_sources'][os_name]:
        if isinstance(os_sources, dict):
            sources = os_sources.get(os_code_name, [])
        else:
            sources = [os_sources]
        if not sources:
            print(
                'WARNING: No sources for %s' % (fmt_os(os_name, os_code_name)),
                 file=sys.stderr)
        for source in sources:
            for p in source.enumerate_packages(os_name, os_code_name, os_arch):
                if p.filelist and file_list_set.intersection(p.filelist):
                    packages.add(p)
    return packages

def _find_packages_with_description(config, description, os_name, os_code_name, os_arch):
    """
    Find packages by description for the given platform.
    """
    packages = set()
    if os_name not in config['package_sources']:
        return set()
    for os_sources in config['package_sources'][os_name]:
        if isinstance(os_sources, dict):
            sources = os_sources.get(os_code_name, [])
        else:
            sources = [os_sources]
        if not sources:
            print(
                'WARNING: No sources for %s' % (fmt_os(os_name, os_code_name)),
                 file=sys.stderr)
        for source in sources:
            for p in source.enumerate_packages(os_name, os_code_name, os_arch):
                pkg_desc = p.description
                if isinstance(pkg_desc, list):
                    pkg_desc = ' '.join(pkg_desc)
                if pkg_desc and description.lower().strip() in pkg_desc.lower().strip():
                    packages.add(p)
    return packages


def _apply_name_replacements(config, package_name: str, os_name: str, os_version: str) -> str:
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
        name_replacements = config.get('name_replacements', {}).get(os_name, {}).get(os_version, {})

        for needle, haystack in name_replacements.items():
            actual_package_name = actual_package_name.replace(needle, haystack)

        return actual_package_name

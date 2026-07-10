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

import os
import tarfile
import time

from .. import open_compressed_url, get_url_hash
from ..package_info import PackageEntry
from .. import RepositoryCacheCollection
from ..tools.file_signature_extractor import get_component_identifier
from ..tools.cache import save_json_cache, load_json_cache
from ..tools import logger

def parse_apkindex(f):
    # An example of an APKINDEX entry. Entries are divided by a blank line.

    # V:2.1.1-r0
    # A:x86_64
    # S:6645
    # I:28672
    # T:RTP proxy (documentation)
    # U:https://www.rtpproxy.org/
    # L:BSD-2-CLause
    # o:rtpproxy
    # m:Natanael Copa <ncopa@alpinelinux.org>
    # t:1602354892
    # c:183e99f73bb1223768aa7231a836a3e98e94c03e
    # i:docs rtpproxy=2.1.1-r0
    # p:alias-of-rtpproxy=2.1.1-r0

    while True:
        entry = {}
        while True:
            l = f.readline().decode('utf-8')
            if l in ['', '\n']:
                break
            k, v = l.strip().split(':', 1)
            entry[k] = v

        if entry:
            yield entry
        else:
            break


class Dependency:
    """
    Dependency class represents apk (Alpine Package) dependency information.
    """

    type = None
    """
    :ivar: the type of the Dependency.
           e.g.
           - None: package
           - 'cmd': command
           - 'so': shared object
    """

    name = None
    """
    :ivar: the name of the Dependency.
    """

    version = None
    """
    :ivar: the version of the Dependency.
    """

    def __init__(self, item):
        try:
            self.type, self.name = item.split(':', 1)
        except (ValueError):
            self.name = item
        try:
            self.name, self.version = self.name.split('=', 1)
        except (ValueError):
            pass


def parse_deps(text):
    return [Dependency(item) for item in text.split(' ')]


_APK_FILELISTS_CACHE = {}
_APK_PKG_FILELIST_CACHE = {}


def build_apk_filelists_map(base_url, os_code_name, os_arch, return_paths=False):
    """Build mapping from package name to list of files using APK package files.
    
    Args:
        base_url: the apk repository base URL.
        os_code_name: the OS version associated with the repository.
        os_arch: the system architecture associated with the repository.
        return_paths: If True, return original paths instead of component_ids.
    
    Returns:
        {pkg_name: [file_identifiers]} - identifiers are component_ids or original paths
    """
    # Cache key includes return_paths mode
    path_mode = "fullpath" if return_paths else "ids"
    url_hash = get_url_hash(base_url)[:8]
    cache_key = f"{os_code_name}_{os_arch}_{url_hash}_{path_mode}"
    
    # Check in-memory cache
    mem_cache_key = (base_url, os_code_name, os_arch, return_paths)
    cached = _APK_FILELISTS_CACHE.get(mem_cache_key)
    if cached is not None:
        return cached
    
    # Try persistent cache using cache.py
    cached = load_json_cache("apk_filelists", cache_key, subdir="filelist")
    if cached is not None:
        logger.info(f"Loaded APK filelists from cache")
        _APK_FILELISTS_CACHE[mem_cache_key] = cached
        return cached
    
    logger.info(f"Building APK filelists map for {os_code_name}/{os_arch} (mode={path_mode})...")
    
    # Get the APKINDEX to find all packages
    apkindex_url = os.path.join(base_url, os_arch, 'APKINDEX.tar.gz')
    filelists_map = {}
    
    try:
        with open_compressed_url(apkindex_url) as f:
            with tarfile.open(mode='r|', fileobj=f) as tf:
                index = None
                for ti in tf:
                    if ti.name == 'APKINDEX':
                        index = tf.extractfile(ti)
                        break
                if index is None:
                    print("Warning: failed to locate APKINDEX in '%s'" % apkindex_url)
                    return {}
                
                # Parse APKINDEX to get package names and versions
                for index_entry in parse_apkindex(index):
                    pkg_name = index_entry.get('P')
                    pkg_version = index_entry.get('V')
                    if not pkg_name or not pkg_version:
                        continue
                    
                    # Try to download and parse the actual APK package to get file list
                    pkg_filename = '%s-%s.apk' % (pkg_name, pkg_version)
                    pkg_url = os.path.join(base_url, os_arch, pkg_filename)
                    
                    try:
                        filelist = _extract_apk_filelist(pkg_url, return_paths=return_paths)
                        logger.info(f"Filelist for {pkg_name}: {filelist}")
                        if filelist:
                            filelists_map[pkg_name] = filelist
                    except Exception as e:
                        # Skip packages that can't be downloaded/parsed
                        continue
                        
    except Exception as e:
        print("Warning: failed to read APKINDEX from '%s': %s" % (apkindex_url, str(e)))
        return {}
    
    # Save to caches
    _APK_FILELISTS_CACHE[mem_cache_key] = filelists_map
    save_json_cache("apk_filelists", cache_key, filelists_map, subdir="filelist")
    
    return filelists_map


def _extract_apk_filelist(pkg_url, return_paths=False):
    """Extract file list from an APK package.
    
    Args:
        pkg_url: URL to the APK package file.
        return_paths: If True, return original paths instead of component_ids.
    
    Returns:
        List of file identifiers (component_ids or original paths)
    """
    try:
        # Cache key must include return_paths to avoid mixing modes.
        path_mode = "fullpath" if return_paths else "ids"
        url_hash = get_url_hash(pkg_url)[:16]
        cache_key = f"{url_hash}_{path_mode}"

        # Check in-memory cache first
        mem_cache_key = (pkg_url, return_paths)
        cached = _APK_PKG_FILELIST_CACHE.get(mem_cache_key)
        if cached is not None:
            return cached

        # Then try persistent cache in dedicated directory: apk_filelist
        cached = load_json_cache("apk_filelist", cache_key, subdir="apk_filelist")
        if cached is not None:
            _APK_PKG_FILELIST_CACHE[mem_cache_key] = cached
            return cached

        # Fallback: if requesting ids but fullpath cache exists, convert it
        if not return_paths:
            fullpath_list = _extract_apk_filelist(pkg_url, return_paths=True)
            if fullpath_list:
                filelist = [r for p in fullpath_list if (r := get_component_identifier(p, return_original=False))]
                _APK_PKG_FILELIST_CACHE[mem_cache_key] = filelist
                save_json_cache("apk_filelist", cache_key, filelist, subdir="apk_filelist")
                return filelist

        logger.info(f"Extracting filelist from {pkg_url}")
        with open_compressed_url(pkg_url, use_cache=False) as f:
            time.sleep(0.2)
            with tarfile.open(mode='r:gz', fileobj=f) as tf:
                filelist = set()
                for ti in tf:
                    # Skip metadata files and directories
                    if ti.name in ['.PKGINFO', '.SIGN.RSA.alpine-devel@lists.alpinelinux.org-6165ee59.rsa.pub']:
                        continue
                    if ti.isdir():
                        continue
                    
                    # Extract file path from tar entry name
                    file_path = '/' + ti.name
                    result = get_component_identifier(file_path, return_original=return_paths)
                    if result:
                        filelist.add(result)

                # Caller does not care about ordering; keep as-is (set -> list).
                result_list = list(filelist)
                _APK_PKG_FILELIST_CACHE[mem_cache_key] = result_list
                save_json_cache("apk_filelist", cache_key, result_list, subdir="apk_filelist")
                return result_list
    except Exception as e:
        print(f"Error extracting filelist from {pkg_url}: {e}")
        return []


def enumerate_apk_packages(base_url, os_name, os_code_name, os_arch, return_paths=False):
    """
    Enumerate packages in an apk (Alpine Package) repository.

    :param base_url: the apk repository base URL.
    :param os_name: the name of the OS associated with the repository.
    :param os_code_name: the OS version associated with the repository.
    :param os_arch: the system architecture associated with the repository.
    :param return_paths: Ignored for APK (filelists are lazily extracted with component_ids).

    :returns: an enumeration of package entries.
    """

    base_url = base_url.replace('$releasever', os_code_name)
    apkindex_url = os.path.join(base_url, os_arch, 'APKINDEX.tar.gz')
    print('Reading apk package metadata from ' + apkindex_url)

    with open_compressed_url(apkindex_url) as f:
        with tarfile.open(mode='r|', fileobj=f) as tf:
            index = None
            for ti in tf:
                if ti.name == 'APKINDEX':
                    index = tf.extractfile(ti)
                    break
            if index is None:
                raise RuntimeError('APKINDEX url did not contain an APKINDEX file')

            for index_entry in parse_apkindex(index):
                pkg_name, pkg_version, source_name = index_entry['P'], index_entry['V'], index_entry['o']
                pkg_description = index_entry.get('T')  # T field contains the description
                pkg_filename = '%s-%s.apk' % (pkg_name, pkg_version)
                pkg_url = os.path.join(base_url, os_arch, pkg_filename)
                # APK filelists are lazily loaded, always use component_id mode
                yield PackageEntry(pkg_name, pkg_version, pkg_url, source_name=source_name, description=pkg_description, filelist=None)

                if 'p' in index_entry:
                    for d in parse_deps(index_entry['p']):
                        if d.type is None:
                            yield PackageEntry(d.name, pkg_version, pkg_url, source_name=source_name, binary_name=pkg_name, description=pkg_description, filelist=None)


def apk_base_url(base_url, return_paths=False):
    """
    Create an enumerable cache for an apk (Alpine Package) repository.

    :param base_url: the URL of the apk repository.
    :param return_paths: Ignored for APK (filelists always use component_ids).

    :returns: an enumerable repository cache instance.
    """
    # Note: return_paths parameter is accepted for API compatibility but not used
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_apk_packages(base_url, os_name, os_code_name, os_arch, return_paths))

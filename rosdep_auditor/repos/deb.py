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

from .. import open_gz_url, get_url_hash
from ..package_info import PackageEntry
from .. import RepositoryCacheCollection
from ..tools.file_signature_extractor import get_component_identifier
from ..tools.cache import save_json_cache, load_json_cache

def enumerate_blocks(url):
    """
    Enumerate blocks of mapped data from a URL to a text file.

    :param url: the URL of the text file.

    :returns: an enumeration of mappings.
    """
    block = {}
    key = None
    with open_gz_url(url) as f:
        while True:
            line = f.readline().decode('utf-8')
            if not len(line):
                break
            elif line[0] in ['\r', '\n']:
                yield block
                block = {}
                key = None
                continue
            elif line[0] in [' ', '\t']:
                # This is a list element
                if not key:
                    raise ValueError('list element at block beginning')
                if not isinstance(block[key], list):
                    block[key] = [block[key]] if block[key] else []
                block[key].append(line.strip())
                continue
            key, val = line.split(':', 1)
            key = key.strip()
            val = val.strip()
            if not key:
                raise ValueError('empty key')
            block[key] = val
    if block:
        yield block


_DEB_CONTENTS_CACHE = {}


def _build_deb_contents_map(base_url, comp, os_code_name, os_arch, return_paths=False):
    """
    Build a mapping of package -> list of files from Debian/Ubuntu Contents index.

    The standard layout is:
      dists/<codename>/<component>/Contents-<arch>.gz

    Lines are formatted as:
      <path> <whitespace> <pkg>[,<pkg>...]
    
    Args:
        base_url: the debian repository base URL.
        comp: the component of the repository (e.g., 'main').
        os_code_name: the OS version associated with the repository.
        os_arch: the system architecture associated with the repository.
        return_paths: If True, return original paths instead of component_ids.
    
    Returns:
        {pkg_name: [file_identifiers]} - identifiers are component_ids or original paths
    """
    path_mode = "fullpath" if return_paths else "ids"
    url_hash = get_url_hash(base_url)[:8]
    is_debian = "debian" in base_url.lower()
    if is_debian:
        cache_component = comp.replace('/', '_')
        cache_key = f"{os_code_name}_{cache_component}_{os_arch}_{url_hash}_{path_mode}"
        mem_cache_key = (base_url, comp, os_code_name, os_arch, return_paths)
    else:
        cache_key = f"{os_code_name}_{os_arch}_{url_hash}_{path_mode}"
        mem_cache_key = (base_url, os_code_name, os_arch, return_paths)
    
    # Check in-memory cache
    cached = _DEB_CONTENTS_CACHE.get(mem_cache_key)
    if cached is not None:
        return cached
    
    # Try persistent cache using cache.py
    cached = load_json_cache("deb_filelists", cache_key, subdir="filelist")
    if cached is not None:
        _DEB_CONTENTS_CACHE[mem_cache_key] = cached
        return cached

    # Fallback: if requesting ids but fullpath cache exists, convert it
    if not return_paths:
        fullpath_map = _build_deb_contents_map(base_url, comp, os_code_name, os_arch, return_paths=True)
        if fullpath_map:
            converted_map = {
                pkg: [r for p in files if (r := get_component_identifier(p, return_original=False))]
                for pkg, files in fullpath_map.items()
            }
            _DEB_CONTENTS_CACHE[mem_cache_key] = converted_map
            save_json_cache("deb_filelists", cache_key, converted_map, subdir="filelist")
            return converted_map

    print(f"Building Deb contents map for {os_code_name}/{os_arch} (mode={path_mode})...")
    
    candidate_urls = [
        os.path.join(base_url, 'dists', os_code_name, comp, f'Contents-{os_arch}.gz'),
    ]
    if is_debian:
        candidate_urls.append(
            os.path.join(base_url, 'dists', os_code_name, comp, 'Contents-all.gz')
        )
    else:
        candidate_urls.append(
            os.path.join(base_url, 'dists', os_code_name, f'Contents-{os_arch}.gz')
        )

    # Use sets to deduplicate if multiple indexes overlap
    filelists_map_sets = {}

    for contents_url in candidate_urls:
        try:
            print('Reading debian contents index from ' + contents_url)
            with open_gz_url(contents_url) as f:
                while True:
                    line = f.readline().decode('utf-8', errors='ignore')
                    if not line:
                        break
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    try:
                        path, pkgs_str = line.rsplit(None, 1)
                    except ValueError:
                        continue
                    # Debian Contents paths don't start with '/', so add it for comparison
                    full_path = '/' + path if not path.startswith('/') else path
                    for pkg in pkgs_str.split(','):
                        token = pkg.strip()
                        if not token:
                            continue
                        # Normalize token to bare binary package name:
                        # - drop multi-arch qualifier (pkg:amd64)
                        # - drop component/section prefix (e.g., universe/text/pkg)
                        token = token.split(':', 1)[0]
                        token = token.split('/')[-1]
                        if not token:
                            continue
                        result = get_component_identifier(full_path, return_original=return_paths)
                        if not result:
                            continue
                        s = filelists_map_sets.setdefault(token, set())
                        s.add(result)
        except Exception as e:
            print('Warning: failed to read debian contents index from %s: %s' % (contents_url, str(e)))

    # Convert sets to lists
    filelists_map = {pkg: list(files) for pkg, files in filelists_map_sets.items()}
    
    # Save to caches
    _DEB_CONTENTS_CACHE[mem_cache_key] = filelists_map
    save_json_cache("deb_filelists", cache_key, filelists_map, subdir="filelist")
    
    return filelists_map


def enumerate_deb_packages(base_url, comp, os_code_name, os_arch, return_paths=False):
    """
    Enumerate debian packages in a repository.

    :param base_url: the debian repository base URL.
    :param comp: the component of the repository to enumerate.
    :param os_code_name: the OS version associated with the repository.
    :param os_arch: the system architecture associated with the repository.
    :param return_paths: If True, return original paths in filelist; otherwise return component_ids.

    :returns: an enumeration of package entries.
    """
    pkgs_url = os.path.join(base_url, 'dists', os_code_name,
                            comp, 'binary-' + os_arch, 'Packages.gz')
    print('Reading debian package metadata from ' + pkgs_url)
    # Build contents map once for this component/arch
    contents_map = _build_deb_contents_map(base_url, comp, os_code_name, os_arch, return_paths=return_paths)
    for block in enumerate_blocks(pkgs_url):
        pkg_url = os.path.join(base_url, block['Filename'])
        pkg_name = block['Package']
        filelist = contents_map.get(pkg_name)
        yield PackageEntry(
            pkg_name,
            block['Version'],
            pkg_url,
            block.get('Source', pkg_name),
            description=block.get('Description'),
            filelist=filelist,
        )


def deb_base_url(base_url, comp, return_paths=False):
    """
    Create an enumerable cache for a debian repository.

    :param base_url: the URL of the debian repository.
    :param comp: the component of the repository.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumerable repository cache instance.
    """
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_deb_packages(base_url, comp, os_code_name, os_arch, return_paths))

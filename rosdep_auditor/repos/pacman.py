# Copyright (c) 2022, Open Source Robotics Foundation
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
import requests
from bs4 import BeautifulSoup
import time

from ..tools import logger

from .. import open_compressed_url, get_url_hash
from ..package_info import PackageEntry
from .. import RepositoryCacheCollection
from ..tools.file_signature_extractor import get_component_identifier
from ..tools.cache import save_json_cache, load_json_cache


def replace_tokens(string, repo_name, os_arch):
    """Replace pacman-specific tokens in the repository base URL."""
    for key, value in {
        '$arch': os_arch,
        '$repo': repo_name,
    }.items():
        string = string.replace(key, value)
    return string


def enumerate_descs(url):
    """
    Enumerate desc files from a pacman db.

    :param url: the URL of the pacman db.

    :returns: an enumeration of desc file contents.
    """
    with open_compressed_url(url) as f:
        with tarfile.open(mode='r|', fileobj=f) as tf:
            for ti in tf:
                if ti.name.endswith('/desc'):
                    yield tf.extractfile(ti)


def enumerate_blocks(url):
    """
    Enumerate blocks of mapped data from a pacman db.

    :param url: the URL of the pacman db.

    :returns: an enumeration of mappings.
    """
    for desc in enumerate_descs(url):
        block = {}
        while True:
            k = desc.readline()
            if not k:
                break
            k = k.strip().decode()
            if not k:
                continue

            v = []
            while True:
                line = desc.readline().strip().decode()
                if not line:
                    break
                v.append(line)

            block[k] = v

        if block: 
            yield block


_PACMAN_FILELISTS_CACHE = {}

def get_filelist_online(pkg_name, repo_name, os_arch, return_paths=False):
    """Get filelist from online Arch Linux package files page.
    
    :param pkg_name: the package name
    :param repo_name: the repository name (e.g., 'extra', 'core', 'community')
    :param os_arch: the system architecture (e.g., 'x86_64')
    :param return_paths: If True, return original paths instead of component_ids.
    :returns: list of file paths or None if failed
    """
    try:
        # Construct the URL for the package files page
        candidate_urls = [
            f"https://archlinux.org/packages/{repo_name}/any/{pkg_name}/files",
            f"https://archlinux.org/packages/{repo_name}/{os_arch}/{pkg_name}/files",
        ]
        
        # Fetch the HTML content
        for candidate_url in candidate_urls:
            response = requests.get(candidate_url, timeout=10)
            time.sleep(0.2)
            if response.status_code == 200:
                break
        if response.status_code != 200:
            return None
        
        # Parse the HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract the file list from the specific div containing package files
        file_list = set()
        pkgfilelist_div = soup.find('div', {'id': 'pkgfilelist'})
        
        if pkgfilelist_div:
            file_ul = pkgfilelist_div.find('ul')
            if file_ul:
                # Iterate over list items with class 'f' (files)
                for li in file_ul.find_all('li', class_='f'):
                    file_path = li.text.strip()
                    if file_path:  # Only add non-empty paths
                        result = get_component_identifier(file_path, return_original=return_paths)
                        if result:
                            file_list.add(result)
        logger.info(f"Filelist for {pkg_name} from {repo_name}/{os_arch}: {file_list}")
        return list(file_list) if file_list else None
        
    except requests.RequestException as e:
        # Handle network errors
        print(f"Warning: failed to fetch filelist for {pkg_name} from {repo_name}/{os_arch}: {e}")
        return None
    except Exception as e:
        # Handle parsing errors
        print(f"Warning: failed to parse filelist for {pkg_name} from {repo_name}/{os_arch}: {e}")
        return None


def _parse_desc_file(desc_fp):
    """Parse */desc in pacman db to get package name."""
    name = None
    while True:
        k = desc_fp.readline()
        if not k:
            break
        k = k.strip().decode(errors="replace")
        if not k:
            continue

        vals = []
        while True:
            line = desc_fp.readline()
            if not line:
                break
            s = line.strip().decode(errors="replace")
            if not s:
                break
            vals.append(s)

        if k == "%NAME%" and vals:
            name = vals[0]
    return name

def _parse_files_file_to_components(files_fp, return_paths=False):
    """
    Parse */files and return a set of component identifiers or original paths.
    Only collect entries under %FILES% block.
    
    Args:
        files_fp: File pointer to the files file.
        return_paths: If True, return original paths instead of component_ids.
    """
    in_files = False
    out = set()

    while True:
        line = files_fp.readline()
        if not line:
            break
        s = line.strip().decode(errors="replace")

        if not s:
            if in_files:
                break
            continue

        if s == "%FILES%":
            in_files = True
            continue

        if in_files:
            # Pacman %FILES% lists directories with a trailing slash. They are redundant noise.
            if s.endswith("/"):
                continue
            result = get_component_identifier(s, return_original=return_paths)
            if result:
                out.add(result)

    return out

def build_pacman_filelists_map(base_url, repo_name, os_arch, return_paths=False):
    """
    Build mapping pkg_name -> list[file_identifiers] by downloading <repo>.files.tar.*
    
    Args:
        base_url: the pacman repository base URL.
        repo_name: the name of the repository (e.g., 'core', 'extra').
        os_arch: the system architecture associated with the repository.
        return_paths: If True, return original paths instead of component_ids.
    
    Returns:
        {pkg_name: [file_identifiers]} - identifiers are component_ids or original paths
    """
    # Cache key includes return_paths mode
    path_mode = "fullpath" if return_paths else "ids"
    url_hash = get_url_hash(base_url)[:8]
    cache_key = f"{repo_name}_{os_arch}_{url_hash}_{path_mode}"
    
    # Check in-memory cache
    mem_cache_key = (base_url, repo_name, os_arch, return_paths)
    cached = _PACMAN_FILELISTS_CACHE.get(mem_cache_key)
    if cached is not None:
        return cached

    # Try persistent cache using cache.py
    cached = load_json_cache("pacman_filelists", cache_key, subdir="filelist")
    if cached is not None:
        _PACMAN_FILELISTS_CACHE[mem_cache_key] = cached
        return cached

    # Fallback: if requesting ids but fullpath cache exists, convert it
    if not return_paths:
        fullpath_map = build_pacman_filelists_map(base_url, repo_name, os_arch, return_paths=True)
        if fullpath_map:
            converted_map = {
                pkg: [r for p in files if (r := get_component_identifier(p, return_original=False))]
                for pkg, files in fullpath_map.items()
            }
            _PACMAN_FILELISTS_CACHE[mem_cache_key] = converted_map
            save_json_cache("pacman_filelists", cache_key, converted_map, subdir="filelist")
            return converted_map

    logger.info(f"Building Pacman filelists map for {repo_name}/{os_arch} (mode={path_mode})...")

    # Try zst first, fall back to gz
    try:
        files_db_url = os.path.join(base_url, f"{repo_name}.files.tar.zst")
        with open_compressed_url(files_db_url) as _:
            pass
    except Exception:
        files_db_url = os.path.join(base_url, f"{repo_name}.files.tar.gz")

    pkgdir_to_name = {}
    pkgdir_to_components = {}

    with open_compressed_url(files_db_url) as f:
        with tarfile.open(mode="r|*", fileobj=f) as tf:
            for ti in tf:
                if not ti.isfile():
                    continue

                if ti.name.endswith("/desc"):
                    fp = tf.extractfile(ti)
                    if not fp:
                        continue
                    name = _parse_desc_file(fp)
                    if name:
                        pkgdir = ti.name.rsplit("/", 1)[0]
                        pkgdir_to_name[pkgdir] = name

                elif ti.name.endswith("/files"):
                    fp = tf.extractfile(ti)
                    if not fp:
                        continue
                    comps = _parse_files_file_to_components(fp, return_paths=return_paths)
                    if comps:
                        pkgdir = ti.name.rsplit("/", 1)[0]
                        pkgdir_to_components[pkgdir] = comps

    filelists_map = {}
    for pkgdir, comps in pkgdir_to_components.items():
        name = pkgdir_to_name.get(pkgdir)
        if name:
            filelists_map[name] = list(comps)
    
    # Save to caches
    _PACMAN_FILELISTS_CACHE[mem_cache_key] = filelists_map
    save_json_cache("pacman_filelists", cache_key, filelists_map, subdir="filelist")

    return filelists_map
  
def enumerate_pacman_packages(base_url, repo_name, os_arch, return_paths=False):
    """
    Enumerate pacman packages in a repository.

    :param base_url: the pacman repository base URL.
    :param repo_name: the name of the repository to enumerate.
    :param os_arch: the system architecture associated with the repository.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumeration of package entries.
    """
    base_url = replace_tokens(base_url, repo_name, os_arch)
    db_url = os.path.join(base_url, repo_name + '.db.tar.gz')
    print('Reading pacman package metadata from ' + db_url)

    # Build filelists map once for this repository
    filelists_map = build_pacman_filelists_map(base_url, repo_name, os_arch, return_paths=return_paths)

    for block in enumerate_blocks(db_url):
        pkg_url = os.path.join(base_url, block['%FILENAME%'][0])
        pkg_name = block['%NAME%'][0]
        pkg_ver = block['%VERSION%'][0]
        pkg_description = block.get('%DESC%', [None])[0]  # %DESC% field contains the description
        pkg_filelist = filelists_map.get(pkg_name)
        yield PackageEntry(pkg_name, pkg_ver, pkg_url, description=pkg_description, filelist=pkg_filelist)
        for pkg_prov in block.get('%PROVIDES%', ()):
            yield PackageEntry(pkg_prov, pkg_ver, pkg_url, pkg_name, pkg_name, description=pkg_description, filelist=pkg_filelist)


def pacman_base_url(base_url, repo_name, return_paths=False):
    """
    Create an enumerable cache for a pacman repository.

    :param base_url: the URL of the pacman repository.
    :param repo_name: the name of the repository to enumerate.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumerable repository cache instance.
    """
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_pacman_packages(base_url, repo_name, os_arch, return_paths))

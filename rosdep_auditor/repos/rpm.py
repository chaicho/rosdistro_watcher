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
from xml.etree import ElementTree

from .. import open_compressed_url, get_url_hash
from ..package_info import PackageEntry
from .. import RepositoryCacheCollection
from .. import URLError
from ..tools.file_signature_extractor import get_component_identifier
from ..tools.cache import save_json_cache, load_json_cache


def replace_tokens(string, os_name, os_code_name, os_arch):
    """Replace RPM-specific tokens in the repository base URL."""
    for key, value in {
        '$basearch': os_arch,
        '$distname': os_name,
        '$releasever': os_code_name,
    }.items():
        string = string.replace(key, value)
    return string


def get_primary_name(repomd_url):
    """Get the URL of the 'primary' metadata from the 'repo' metadata."""
    print('Reading RPM repository metadata from ' + repomd_url)
    with open_compressed_url(repomd_url) as f:
        tree = iter(ElementTree.iterparse(f, events=('start', 'end')))
        event, root = next(tree)
        if root.tag != '{http://linux.duke.edu/metadata/repo}repomd':
            raise RuntimeError('Invalid root element in repository metadata: ' + root.tag)
        for event, root_child in tree:
            if (
                root_child.tag != '{http://linux.duke.edu/metadata/repo}data' or
                root_child.attrib.get('type', '') != 'primary'
            ):
                root.clear()
                continue
            for data_child in root_child:
                if (
                    data_child.tag != '{http://linux.duke.edu/metadata/repo}location' or
                    'href' not in data_child.attrib
                ):
                    root.clear()
                    continue
                return data_child.attrib['href']
            root.clear()
    raise RuntimeError('Failed to determine primary data file name')


def get_filelists_name(repomd_url):
    """Get the file name (href) of the 'filelists' metadata from repomd.xml."""
    print('Reading RPM repository metadata from ' + repomd_url)
    with open_compressed_url(repomd_url) as f:
        tree = iter(ElementTree.iterparse(f, events=('start', 'end')))
        event, root = next(tree)
        if root.tag != '{http://linux.duke.edu/metadata/repo}repomd':
            raise RuntimeError('Invalid root element in repository metadata: ' + root.tag)
        for event, root_child in tree:
            if (
                root_child.tag != '{http://linux.duke.edu/metadata/repo}data' or
                root_child.attrib.get('type', '') != 'filelists'
            ):
                root.clear()
                continue
            for data_child in root_child:
                if (
                    data_child.tag != '{http://linux.duke.edu/metadata/repo}location' or
                    'href' not in data_child.attrib
                ):
                    root.clear()
                    continue
                return data_child.attrib['href']
            root.clear()
    raise RuntimeError('Failed to determine filelists data file name')


_RPM_FILELISTS_CACHE = {}


def build_rpm_filelists_map(base_url, os_name, os_code_name, os_arch, return_paths=False):
    """Build mapping from package name to list of files using RPM filelists.xml.
    
    Args:
        base_url: the RPM repository base URL.
        os_name: the name of the OS associated with the repository.
        os_code_name: the OS version associated with the repository.
        os_arch: the system architecture associated with the repository.
        return_paths: If True, return original paths instead of component_ids.
    
    Returns:
        {pkg_name: [file_identifiers]} - identifiers are component_ids or original paths
    """
    base_url = replace_tokens(base_url, os_name, os_code_name, os_arch)
    
    # Cache key includes return_paths mode
    path_mode = "fullpath" if return_paths else "ids"
    url_hash = get_url_hash(base_url)[:8]
    cache_key = f"{os_code_name}_{os_arch}_{url_hash}_{path_mode}"
    
    # Check in-memory cache
    mem_cache_key = (base_url, os_code_name, os_arch, return_paths)
    cached = _RPM_FILELISTS_CACHE.get(mem_cache_key)
    if cached is not None:
        return cached
    
    # Try persistent cache using cache.py
    cached = load_json_cache("rpm_filelists", cache_key, subdir="filelist")
    if cached is not None:
        _RPM_FILELISTS_CACHE[mem_cache_key] = cached
        return cached

    # Fallback: if requesting ids but fullpath cache exists, convert it
    if not return_paths:
        fullpath_map = build_rpm_filelists_map(base_url, os_name, os_code_name, os_arch, return_paths=True)
        if fullpath_map:
            converted_map = {
                pkg: [r for p in files if (r := get_component_identifier(p, return_original=False))]
                for pkg, files in fullpath_map.items()
            }
            _RPM_FILELISTS_CACHE[mem_cache_key] = converted_map
            save_json_cache("rpm_filelists", cache_key, converted_map, subdir="filelist")
            return converted_map

    print(f"Building RPM filelists map for {os_code_name}/{os_arch} (mode={path_mode})...")
    repomd_url = os.path.join(base_url, 'repodata', 'repomd.xml')
    try:
        filelists_name = get_filelists_name(repomd_url)
    except Exception as e:
        print("Warning: failed to locate RPM filelists in repomd '%s': %s" % (repomd_url, str(e)))
        return {}
    filelists_url = os.path.join(base_url, filelists_name)
    print('Reading RPM filelists metadata from ' + filelists_url)

    filelists_sets = {}
    try:
        with open_compressed_url(filelists_url) as f:
            ns = '{http://linux.duke.edu/metadata/filelists}'
            context = ElementTree.iterparse(f, events=('start', 'end'))
            current_pkg_name = None
            for event, elem in context:
                if event == 'start' and elem.tag == ns + 'package':
                    current_pkg_name = elem.attrib.get('name')
                    if current_pkg_name:
                        filelists_sets.setdefault(current_pkg_name, set())
                elif event == 'end' and elem.tag == ns + 'file':
                    if current_pkg_name:
                        # RPM filelists can include directory entries: <file type="dir">...</file>
                        # They are redundant and add noise to similarity/signature computation.
                        ftype = (elem.attrib or {}).get('type', '')
                        if str(ftype).lower() == 'dir':
                            elem.clear()
                            continue
                        text = (elem.text or '')
                        if text:
                            path_str = text.strip()
                            # Some repos may serialize directories with trailing slash; drop them too.
                            if path_str.endswith('/'):
                                elem.clear()
                                continue
                            result = get_component_identifier(path_str, return_original=return_paths)
                            if result:
                                filelists_sets[current_pkg_name].add(result)
                    elem.clear()
                elif event == 'end' and elem.tag == ns + 'package':
                    elem.clear()
                    current_pkg_name = None
    except Exception as e:
        print("Warning: failed to read RPM filelists from '%s': %s" % (filelists_url, str(e)))
    
    # Convert to lists
    filelists_map = {pkg: list(files) for pkg, files in filelists_sets.items()}

    # Save to caches
    _RPM_FILELISTS_CACHE[mem_cache_key] = filelists_map
    save_json_cache("rpm_filelists", cache_key, filelists_map, subdir="filelist")
    print(f"Saved RPM filelists map to cache: {cache_key}")
    
    return filelists_map


def enumerate_base_urls(mirrorlist_url):
    """Get candidate RPM repository base URLs from a mirrorlist file."""
    with open_compressed_url(mirrorlist_url) as f:
        while True:
            line = f.readline().decode('utf-8')
            if not len(line):
                break
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            yield line


def enumerate_rpm_packages(base_url, os_name, os_code_name, os_arch, return_paths=False):
    """
    Enumerate packages in an RPM repository.

    :param base_url: the RPM repository base URL.
    :param os_name: the name of the OS associated with the repository.
    :param os_code_name: the OS version associated with the repository.
    :param os_arch: the system architecture associated with the repository.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumeration of package entries.
    """
    base_url = replace_tokens(base_url, os_name, os_code_name, os_arch)
    repomd_url = os.path.join(base_url, 'repodata', 'repomd.xml')
    filelists_map = build_rpm_filelists_map(base_url, os_name, os_code_name, os_arch, return_paths=return_paths)
    primary_xml_name = get_primary_name(repomd_url)
    primary_xml_url = os.path.join(base_url, primary_xml_name)
    print('Reading RPM primary metadata from ' + primary_xml_url)
    with open_compressed_url(primary_xml_url) as f:
        tree = ElementTree.iterparse(f)
        for event, element in tree:
            if (
                element.tag != '{http://linux.duke.edu/metadata/common}package' or
                element.attrib.get('type', '') != 'rpm'
            ):
                continue
            pkg_name = None
            pkg_version = None
            pkg_src_name = None
            pkg_url = None
            pkg_description = None
            pkg_provs = []
            for pkg_child in element:
                if pkg_child.tag == '{http://linux.duke.edu/metadata/common}name':
                    pkg_name = pkg_child.text
                elif pkg_child.tag == '{http://linux.duke.edu/metadata/common}version':
                    pkg_version = pkg_child.attrib.get('ver')
                    if pkg_version:
                        pkg_epoch = pkg_child.attrib.get('epoch', '0')
                        if pkg_epoch != '0':
                            pkg_version = pkg_epoch + ':' + pkg_version
                        pkg_rel = pkg_child.attrib.get('rel')
                        if pkg_rel:
                            pkg_version = pkg_version + '-' + pkg_rel
                elif pkg_child.tag == '{http://linux.duke.edu/metadata/common}summary':
                    pkg_description = pkg_child.text
                elif pkg_child.tag == '{http://linux.duke.edu/metadata/common}location':
                    pkg_href = pkg_child.attrib.get('href')
                    if pkg_href:
                        pkg_url = os.path.join(base_url, pkg_href)
                elif pkg_child.tag == '{http://linux.duke.edu/metadata/common}format':
                    for format_child in pkg_child:
                        if format_child.tag == '{http://linux.duke.edu/metadata/rpm}sourcerpm':
                            if format_child.text:
                                pkg_src_name = '-'.join(format_child.text.split('-')[:-2])
                        if format_child.tag != '{http://linux.duke.edu/metadata/rpm}provides':
                            continue
                        for provides in format_child:
                            if (
                                provides.tag != '{http://linux.duke.edu/metadata/rpm}entry' or
                                'name' not in provides.attrib
                            ):
                                continue
                            prov_version = None
                            if provides.attrib.get('flags', '') == 'EQ':
                                prov_version = provides.attrib.get('ver')
                                if prov_version:
                                    prov_epoch = provides.attrib.get('epoch', '0')
                                    if prov_epoch != '0':
                                        prov_version = prov_epoch + ':' + prov_version
                                    prov_rel = provides.attrib.get('rel')
                                    if prov_rel:
                                        prov_version = prov_version + '-' + prov_rel
                            pkg_provs.append((provides.attrib['name'], prov_version))
            pkg_filelist = filelists_map.get(pkg_name)
            yield PackageEntry(pkg_name, pkg_version, pkg_url, pkg_src_name, pkg_name, description=pkg_description, filelist=pkg_filelist)
            for prov_name, prov_version in pkg_provs:
                yield PackageEntry(pkg_name, prov_version, pkg_url, pkg_src_name, prov_name, description=pkg_description, filelist=None)
            element.clear()


def enumerate_rpm_packages_from_mirrorlist(mirrorlist_url, os_name, os_code_name, os_arch, return_paths=False):
    """
    Enumerate packages in an RPM repository using a mirrorlist.

    :param mirrorlist_url: the RPM repository mirrorlist file URL.
    :param os_name: the name of the OS associated with the repository.
    :param os_code_name: the OS version associated with the repository.
    :param os_arch: the system architecture associated with the repository.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumeration of package entries.
    """
    mirrorlist_url = replace_tokens(mirrorlist_url, os_name, os_code_name, os_arch)
    print('Reading RPM mirrorlist from ' + mirrorlist_url)
    for base_url in enumerate_base_urls(mirrorlist_url):
        try:
            for pkg in enumerate_rpm_packages(base_url, os_name, os_code_name, os_arch, return_paths):
                yield pkg
            else:
                return
        except Exception as e:
            if not isinstance(e, (
                ConnectionResetError,
                RuntimeError,
                URLError,
            )):
                raise
            print("Error reading from mirror '%s': %s" % (base_url, str(e)))
            print('Falling back to next available mirror...')
            # We may end up re-enumerating some packages, but it's better than
            # erroring out due to a connection reset...
    else:
        raise RuntimeError('All mirrors were tried')


def rpm_base_url(base_url, return_paths=False):
    """
    Create an enumerable cache for an RPM repository.

    :param base_url: the URL of the RPM repository.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumerable repository cache instance.
    """
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_rpm_packages(base_url, os_name, os_code_name, os_arch, return_paths))


def rpm_mirrorlist_url(mirrorlist_url, return_paths=False):
    """
    Create an enumerable cache for an RPM repository mirrorlist.

    :param mirrorlist_url: the URL of the RPM repository mirrorlist file.
    :param return_paths: If True, use full paths in filelist; otherwise use component_ids.

    :returns: an enumerable repository cache instance.
    """
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_rpm_packages_from_mirrorlist(
                mirrorlist_url, os_name, os_code_name, os_arch, return_paths))


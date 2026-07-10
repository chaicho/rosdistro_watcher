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
import re
import yaml

from .tools import logger
from .repos.apk import apk_base_url
from .repos.deb import deb_base_url
from .repos.layer_index import layer_index_url
from .repos.nixos import nixos_base_url
from .repos.gentoo import gentoo_base_url
from .repos.pacman import pacman_base_url
from .repos.ros import ros_base_url
from .repos.rpm import rpm_base_url
from .repos.rpm import rpm_mirrorlist_url


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'configs',
    'config.yaml')

# Latest config path for backward compatible cache key
# Use relative path from module location instead of hardcoded absolute path
LATEST_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', 'configs', 'config_distribution_comparison.yaml')
# Cache for the latest config hash
_latest_config_hash = None

# Add these variables at the top of the file (after imports)
# Module-level cache for config
_cached_config = None
_cached_config_path = None
_cached_configs = {}

# Global setting to control return_paths mode
# Set via environment variable ROSDEP_USE_FULLPATH=true
_USE_FULLPATH = os.getenv('ROSDEP_USE_FULLPATH', 'false').lower() == 'true'


def load_apk_base_url(loader, node):
    # Follow global return_paths mode (ROSDEP_USE_FULLPATH=true -> full paths)
    return apk_base_url(node.value, return_paths=_USE_FULLPATH)


def load_deb_base_url(loader, node):
    base_url, comp = node.value.rsplit(' ', 1)
    return deb_base_url(base_url, comp, return_paths=_USE_FULLPATH)


def load_layer_index_url(loader, node):
    return layer_index_url(node.value)


def load_pacman_base_url(loader, node):
    base_url, repo_name = node.value.rsplit(' ', 1)
    return pacman_base_url(base_url, repo_name, return_paths=_USE_FULLPATH)


def load_rpm_base_url(loader, node):
    return rpm_base_url(node.value, return_paths=_USE_FULLPATH)


def load_rpm_mirrorlist_url(loader, node):
    return rpm_mirrorlist_url(node.value, return_paths=_USE_FULLPATH)


def load_ros_base_url(loader, node):
    base_url, comp = node.value.rsplit(' ', 1)
    # ROS doesn't use filelists, return_paths is ignored
    return ros_base_url(base_url, comp)


def load_nixos_base_url(loader, node):
    return nixos_base_url(node.value)


def load_gentoo_base_url(loader, node):
    return gentoo_base_url(node.value)


def load_regex(loader, node):
    return re.compile(node.value)


yaml.add_constructor(
    u'!apk_base_url', load_apk_base_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!deb_base_url', load_deb_base_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!layer_index_url', load_layer_index_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!nixos_base_url', load_nixos_base_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!pacman_base_url', load_pacman_base_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!ros_base_url', load_ros_base_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!rpm_base_url', load_rpm_base_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!rpm_mirrorlist_url', load_rpm_mirrorlist_url, Loader=yaml.SafeLoader)
yaml.add_constructor(
    u'!regular_expression', load_regex, Loader=yaml.SafeLoader)


def _compute_config_hash(config):
    """Compute hash of config's supported_versions for cache key"""
    import hashlib
    supported_versions = config.get('supported_versions', {})
    # Sort and serialize supported_versions to ensure same content produces same hash
    sorted_versions = dict(sorted(supported_versions.items()))
    for repo_name in sorted_versions:
        sorted_versions[repo_name] = sorted(sorted_versions[repo_name])
    return hashlib.md5(str(sorted_versions).encode('utf-8')).hexdigest()


def get_latest_config_hash():
    """Get config hash from LATEST_CONFIG_PATH for backward compatibility"""
    global _latest_config_hash
    if _latest_config_hash is not None:
        return _latest_config_hash
    try:
        with open(LATEST_CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        _latest_config_hash = _compute_config_hash(config)
        return _latest_config_hash
    except Exception:
        return None


def load_config(path=None, use_cache=True, use_existing_config=True):
    """
    Load configuration from YAML file with optional caching.

    :param path: Path to config file (defaults to DEFAULT_CONFIG_PATH)
    :param use_cache: If True, use cached config if available (default: True)
    :returns: Parsed configuration dictionary
    """
    global _cached_config, _cached_config_path

    if use_existing_config and use_cache and _cached_configs and (not path):
        config_path = list(_cached_configs.keys())[0]
    else:
        config_path = path or DEFAULT_CONFIG_PATH


    # If caching is enabled and we have a cached config for the same path, return it
    if use_cache and config_path in _cached_configs:
        logger.info(f"Using cached config from {config_path}")
        return _cached_configs[config_path]

    # Load fresh config
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Compute config hash and store in config
    config['_hash'] = _compute_config_hash(config)

    # Cache the result if caching is enabled
    if use_cache:
        _cached_configs[config_path] = config
        _cached_config_path = config_path

    return config


def clear_config_cache():
    """Clear the cached configuration."""
    global _cached_configs
    _cached_configs = {}

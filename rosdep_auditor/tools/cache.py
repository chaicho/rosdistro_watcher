#!/usr/bin/env python3
"""
Generic cache utility functions for persisting JSON data to disk.

This module provides reusable functions for saving and loading cache data,
following the pattern used in repos/deb.py and repos/apk.py.
"""

import os
import json
import fcntl
from typing import Any, Optional, Callable

from . import logger

GLOBAL_CACHE_DIR = os.path.expanduser("~/cache/rosdistro_watcher")

# Set ROSDEP_AUDITOR_USE_CACHE=false to bypass all disk caching.
# This forces live data fetches and suppresses cache writes.
_USE_CACHE = os.getenv('ROSDEP_AUDITOR_USE_CACHE', 'true').lower() != 'false'


class _FileLock:
    """Context manager for file locking using fcntl (cross-process safe)."""

    def __init__(self, file_obj, exclusive=False):
        self.file_obj = file_obj
        self.exclusive = exclusive

    def __enter__(self):
        operation = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
        fcntl.flock(self.file_obj.fileno(), operation)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        fcntl.flock(self.file_obj.fileno(), fcntl.LOCK_UN)

def get_cache_file_path(cache_name: str, cache_key: str, 
                       cache_dir: Optional[str] = None, 
                       subdir: Optional[str] = None) -> str:
    """
    Generate cache file path.
    
    Args:
        cache_name: Name of the cache (e.g., "candidates_cache")
        cache_key: String representation of the cache key
        cache_dir: Directory to store cached files (defaults to GLOBAL_CACHE_DIR)
        subdir: Optional subdirectory within cache_dir (e.g., "candidates")
    
    Returns:
        Path to the cache file
    """
    if cache_dir is None:
        cache_dir = GLOBAL_CACHE_DIR
    
    # Build the full cache directory path
    if subdir:
        full_cache_dir = os.path.join(cache_dir, subdir)
    else:
        full_cache_dir = cache_dir
    
    # Ensure cache directory exists
    os.makedirs(full_cache_dir, exist_ok=True)
    
    # Generate filename
    filename = f"{cache_name}_{cache_key}.json"
    return os.path.join(full_cache_dir, filename)


def save_json_cache(cache_name: str, cache_key: str, data: Any,
                   cache_dir: Optional[str] = None,
                   subdir: Optional[str] = None,
                   serialize: Optional[Callable[[Any], Any]] = None) -> str:
    """
    Save data to JSON cache file.
    
    Args:
        cache_name: Name of the cache (e.g., "candidates_cache")
        cache_key: String representation of the cache key
        data: Data to save (will be serialized if serialize function provided)
        cache_dir: Directory to store cached files (defaults to GLOBAL_CACHE_DIR)
        subdir: Optional subdirectory within cache_dir (e.g., "candidates")
        serialize: Optional function to serialize data before saving (e.g., for objects: lambda objs: [obj.to_dict() for obj in objs])
    
    Returns:
        Path to the saved cache file
    
    Raises:
        OSError: If the file cannot be written
        TypeError: If data is not JSON-serializable
    """
    if not _USE_CACHE:
        logger.debug("Cache disabled via ROSDEP_AUDITOR_USE_CACHE=false, skipping save")
        return None

    cache_file = get_cache_file_path(cache_name, cache_key, cache_dir, subdir)

    # Apply serialization if provided
    if serialize is not None:
        data = serialize(data)
    logger.debug(f"Saving cache to {cache_file}")
    try:
        # Open in read-write mode to support shared lock for reading, exclusive for writing
        with open(cache_file, 'w', encoding='utf-8') as f:
            with _FileLock(f, exclusive=True):
                json.dump(data, f, ensure_ascii=False, indent=2)
        return cache_file
    except Exception as e:
        # Re-raise with context
        raise OSError(f"Failed to save cache to {cache_file}: {e}") from e


def load_json_cache(cache_name: str, cache_key: str,
                   cache_dir: Optional[str] = None,
                   subdir: Optional[str] = None,
                   deserialize: Optional[Callable[[Any], Any]] = None) -> Optional[Any]:
    """
    Load data from JSON cache file.
    
    Args:
        cache_name: Name of the cache (e.g., "candidates_cache")
        cache_key: String representation of the cache key
        cache_dir: Directory to check for cached files (defaults to GLOBAL_CACHE_DIR)
        subdir: Optional subdirectory within cache_dir (e.g., "candidates")
        deserialize: Optional function to deserialize loaded data (e.g., for objects: lambda data: [PackageInfo.from_dict(d) for d in data])
    
    Returns:
        Loaded data (deserialized if deserialize function provided), or None if file doesn't exist or is invalid
    """
    if not _USE_CACHE:
        logger.debug("Cache disabled via ROSDEP_AUDITOR_USE_CACHE=false, returning None")
        return None

    cache_file = get_cache_file_path(cache_name, cache_key, cache_dir, subdir)

    if not os.path.exists(cache_file):
        logger.debug(f"Cache file {cache_file} does not exist")
        return None

    logger.debug('Loading cache file: ' + cache_file)
    
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            with _FileLock(f, exclusive=False):  # Shared lock for reading
                data = json.load(f)

        # Apply deserialization if provided
        if deserialize is not None:
            data = deserialize(data)

        return data
    except Exception:
        # Return None on any error (file corruption, invalid JSON, etc.)
        return None

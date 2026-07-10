import argparse
import yaml
import os
import sys
import re
from typing import List

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "rosdep_auditor"

from . import _find_package, _apply_name_replacements
from .distro_parser import RosdepDatabase, DistributionDatabase
from .tools.yaml import AnnotatedSafeLoader
from .tools.logger import log_to_file
from .tools import logger
from .tools.name_pattern import normalize_special_package_name
from .repos.ros import get_ros_package_name
from .package_distribution_detector import PackageDistributionDetector
from .distribution_table import DistributionTable
from .config import load_config

# entry_key -> DistributionTable aggregated cache
_aggregated_distribution_cache = {}

# PackageDistributionDetector instance cache (avoid duplicate creation)
_detector_cache = {}


def _normalize_for_platform_package_compare(package_name: str, platform: str) -> str:
    """
    Normalize package names for platform-specific equivalence checks used by B.1b.

    This keeps the normalization local to the comparison path so we do not
    change global name normalization behavior.
    """
    normalized = normalize_special_package_name(package_name)

    # Normalize python version-specific names like python39 / python311
    # to the generic python3 so they match rosdep entries that use the
    # %{python3_pkgversion} macro (which expands to a bare "3").
    # For example: python39-numpy -> python3-numpy
    normalized = re.sub(r'^python3\d+', 'python3', normalized)

    if platform == 'nixos':
        # Use the leaf attribute name for NixOS comparisons so attrset prefixes
        # like python314Packages., perlPackages., kdePackages., etc. do
        # not trigger B.1b once we have already narrowed to a single target.
        normalized = normalized.split('.')[-1]

    return normalized


def _collect_local_rosdep_yaml_contents(rosdep_dir: str, include_distribution: bool = False) -> dict:
    """
    Read all rosdep YAML files under the given directory and return a mapping
    of filename -> file content.
    """
    contents_by_filename = {}
    for filename in sorted(os.listdir(rosdep_dir)):
        if (filename not in ('python.yaml', 'base.yaml')) and not (include_distribution and 'distribution' in filename):
            continue
        full_path = os.path.join(rosdep_dir, filename)

        if not os.path.isfile(full_path):
            continue
        if not filename.endswith('.yaml'):
            continue
        with open(full_path, 'r', encoding='utf-8') as f:
            contents_by_filename[filename] = f.read()
    return contents_by_filename


def load_yaml_file(file_path: str) -> dict:
    """
    Load a single rosdep YAML file and return it in the format expected by build_rosdep_database.

    Args:
        file_path: Path to the YAML file (e.g., 'rosdep/base.yaml')

    Returns:
        Dictionary with filename as key and file content as value.
    """
    filename = os.path.basename(file_path)
    with open(file_path, 'r', encoding='utf-8') as f:
        return {filename: f.read()}


def build_rosdep_database(config, file_content_dict):
    """Build and return a RosdepDatabase loaded with provided file contents."""
    rosdep_database = RosdepDatabase(config)
    for file_path, content in file_content_dict.items():
        if 'distribution' in file_path:
            distribution_database = DistributionDatabase(config)
            distribution_database.load_yaml(content)
            rosdep_database.load_distribution(distribution_database)
        else:
            rosdep_database.load_yaml(content)
    return rosdep_database


def get_all_entries_from_contents(config, file_content_dict):
    """Helper to list all non-distribution rosdep entries from contents."""
    db = build_rosdep_database(config, file_content_dict)
    return db.get_all_entries()


def get_aggregated_distribution_table_cached(config, rosdep_database, entry_key):
    """
    Get aggregated distribution table for an entry with caching.
    Calculation method is consistent with check_entry_all_defects.
    """
    if entry_key in _aggregated_distribution_cache:
        return _aggregated_distribution_cache[entry_key]

    entry_data = rosdep_database.get_entry(entry_key)
    if entry_data is None:
        table = DistributionTable([])
        _aggregated_distribution_cache[entry_key] = table
        return table

    # Use cached detector instance to avoid duplicate creation
    cache_key = id(config)
    if cache_key not in _detector_cache:
        _detector_cache[cache_key] = PackageDistributionDetector(config)
    detector = _detector_cache[cache_key]

    packages_to_check = entry_data.get_all_packages_from_config(config)
    def get_platform_priority(platform):
        if platform == 'ubuntu':
            return 0
        elif platform == 'debian':
            return 1
        elif platform == 'fedora':
            return 2
        elif platform == 'gentoo':
            return 3
        else:
            return 4
    packages_to_check = sorted(
        packages_to_check,
        key=lambda p: get_platform_priority(p.get('platform'))
    )
    aggregated_dist_table = DistributionTable([])

    for package_to_check in packages_to_check:
        platform = package_to_check['platform']
        os_ver = package_to_check['version']
        package_names = package_to_check['packages']
        package_manager = package_to_check['package_manager']
        if package_manager == 'pip':
            platform = 'pypi'
            os_ver = ''
        for package_name in package_names:
            distribution_table = detector.detect_distribution(
                package_name,
                specific_package=(package_name, platform, os_ver),
            )
            if len(distribution_table) > 0:
                logger.info(f"Use package '{package_name}' in {platform} {os_ver} to build aggregated distribution table")
                aggregated_dist_table.add_distribution_table(distribution_table)
        if len(aggregated_dist_table) > 0:
            break

    _aggregated_distribution_cache[entry_key] = aggregated_dist_table
    return aggregated_dist_table


# ============================================================================
# Unified Entry Defect Detector
# ============================================================================

def _get_os_repo_platforms(config) -> List[str]:
    """Get list of OS repository platforms from config."""
    return sorted(set(config['supported_versions'].keys()) - {'pypi', 'ros'})


def _format_defect_message(defect_type, entry, details) -> str:
    """Format standardized defect message"""
    if defect_type == 'A.1':
        return f"Entry '{entry}' not found in database"
    elif defect_type == 'A.2':
        platform = details.get('platform', '')
        version = details.get('version', '')
        suggested_package = details.get('suggested_package', '')
        return (f"Entry '{entry}' missing version '{version}' for platform '{platform}'; "
                f"suggested package: '{suggested_package}'")
    elif defect_type == 'B.1a':
        if details and 'reason' in details and details['reason'] == 'not_found':
            platform = details.get('platform', '')
            version = details.get('version', '')
            package_name = details.get('package_name', '')
            detected = details.get('detected_package', '')
            if detected:
                return (f"Package '{package_name}' not found in {platform} {version}; "
                        f"detected existing package: '{detected}'")
            return f"Package '{package_name}' not found in {platform} {version}"
        return f"No distribution info found for entry '{entry}'"
    elif defect_type == 'B.1b':
        reason = details.get('reason', 'unknown')
        if reason == 'differs':
            platform = details.get('platform', '')
            version = details.get('version', '')
            package_name = details.get('package_name', '')
            expected = details.get('expected_package', '')
            return (f"Package '{package_name}' differs from expected '{expected}' "
                    f"in {platform} {version}")
        elif reason == 'empty_list':
            platform = details.get('platform', '')
            version = details.get('version', '')
            expected = details.get('expected_package', '')
            return (f"Empty package list for {platform} {version}; "
                    f"expected package: '{expected}'")
        return f"Defect B.1b for entry '{entry}'"
    elif defect_type == 'B.2a':
        current_manager = details.get('current_manager', '')
        suggested_platform = details.get('suggested_platform', '')
        suggested_version = details.get('suggested_version', '')
        suggested_package = details.get('suggested_package', '')
        return (f"Entry '{entry}' using {current_manager} but package '{suggested_package}' "
                f"available in {suggested_platform} {suggested_version}")
    elif defect_type == 'B.2b':
        os_platform = details.get('os_platform', '')
        os_version = details.get('os_version', '')
        os_package = details.get('os_package', '')
        return (f"Distribution entry '{entry}' exists in {os_platform} {os_version} "
                f"as '{os_package}'")
    return f"Defect {defect_type} for entry '{entry}'"


def _add_defect(defects: dict, defect_type: str, entry: str, details: dict) -> None:
    """Create and register a defect with logging."""
    details['type'] = defect_type
    details['entry'] = entry
    details['description'] = _format_defect_message(defect_type, entry, details)
    defects[defect_type].append(details)
    logger.info(f"Type {defect_type}: {details['description']}")
    log_to_file(f"Type {defect_type}: {details['description']}")


def _check_b2a_defect(config, rosdep_database, entry_to_check, defects):
    """
    Check for B.2a defect: python/pip coexistence.

    Args:
        config: the parsed YAML configuration
        rosdep_database: RosdepDatabase instance containing rosdep rules
        entry_to_check: the rosdep entry key to check
        defects: dict to store detected defects (modified in place)
    """
    if entry_to_check.startswith('py') and not entry_to_check.endswith('-pip'):
        base = entry_to_check
        possible_pip_entries = [
            base.replace('python3-', '') + '-pip',
            base.replace('python-', '') + '-pip',
            base.replace('python3', 'python') + '-pip',
            base.replace('python', 'python3') + '-pip',
            'python-' + base + '-pip',
            'python3-' + base + '-pip',
        ]
        for possible_entry in possible_pip_entries:
            possible_entry_data = rosdep_database.get_entry(possible_entry)
            if possible_entry_data is None:
                continue
            
            # Only add B.2a if the current entry has A.1 defect
            has_a1 = any(d['entry'] == entry_to_check for d in defects['A.1'])
            if not has_a1:
                continue

            # Call check_entry_all_defects for the pip entry
            pip_defects = check_entry_all_defects(config, rosdep_database, possible_entry)

            # Extract B.2a defects from the result and merge
            for defect in pip_defects.get('B.2a', []):
                # Update the 'entry' field to point to the original entry (not the pip entry)
                defect['entry'] = entry_to_check
                defect['description'] = _format_defect_message('B.2a', entry_to_check, defect)
                defects['B.2a'].append(defect)
                logger.info(f"Type B.2a: {defect['description']}")
                log_to_file(f"Type B.2a: {defect['description']}")

            if defects['B.2a']:
                break


def check_entry_all_defects(config, rosdep_database, entry_to_check, aggregated_dist_table=None):
    """
    Check all defect types for a single rosdep entry in one pass.

    Returns:
        dict: {defect_type: [defect_list]} containing all defects found
    """
    defects = {
        'A.1': [],
        'A.2': [],
        'B.1a': [],
        'B.1b': [],
        'B.2a': [],
        'B.2b': []
    }

    is_distribution = rosdep_database.is_distribution_entry(entry_to_check)

    if not is_distribution:
        if rosdep_database.get_entry(entry_to_check) is None:
            _add_defect(defects, 'A.1', entry_to_check, {})

            # Check for python/pip coexistence (B.2a) even when entry not found
            _check_b2a_defect(config, rosdep_database, entry_to_check, defects)

            return defects

    entry_data = rosdep_database.get_entry(entry_to_check)

    if is_distribution:
        packages_to_check = []
    else:
        if entry_data is None:
            return defects
        packages_to_check = entry_data.get_all_packages_from_config(config)

    if aggregated_dist_table is None:
        aggregated_dist_table = get_aggregated_distribution_table_cached(
            config, rosdep_database, entry_to_check
        )

    if len(aggregated_dist_table) == 0 and not is_distribution:
        _add_defect(defects, 'B.1a', entry_to_check, {})
        return defects

    if is_distribution:
        os_repo_platforms = _get_os_repo_platforms(config)
        for os_platform in os_repo_platforms:
            if os_platform in config['supported_versions']:
                for os_version in config['supported_versions'][os_platform]:
                    os_arches = config['supported_arches'].get(os_platform, [])
                    if os_arches:
                        os_arch = os_arches[0]
                        res = _find_package(config, entry_to_check, os_platform, os_version, os_arch)
                        if res:
                            _add_defect(defects, 'B.2b', entry_to_check, {
                                'os_platform': os_platform,
                                'os_version': os_version,
                                'os_package': entry_to_check
                            })
                            break
    else:
        _check_b2a_defect(config, rosdep_database, entry_to_check, defects)

    if not is_distribution and entry_data:
        supported_platforms = [k for k in config['supported_versions'].keys() if k not in {'pypi', 'ros'}]
        
        for platform in supported_platforms:
            if platform == '*':
                continue

            supported_versions = config['supported_versions'].get(platform, [])
            for version in supported_versions:
                package_details = entry_data.get_package_details(platform, version)

                if package_details is None:
                    package_info = aggregated_dist_table.get_first_package_info(platform, version)
                    if package_info:
                        _add_defect(defects, 'A.2', entry_to_check, {
                            'missing_type': 'platform',
                            'platform': platform,
                            'version': version,
                            'suggested_package': package_info.bin_name,
                            'suggested_repo': package_info.repo_name
                        })

                elif package_details == {}:
                    logger.info(f"Case B: Empty package list for {platform} {version}")
                    package_info = aggregated_dist_table.get_first_package_info(platform, version)
                    if package_info:
                        _add_defect(defects, 'B.1b', entry_to_check, {
                            'reason': 'empty_list',
                            'platform': platform,
                            'version': version,
                            'package_name': '',
                            'expected_package': package_info.bin_name
                        })

                else:
                    package_manager = list(package_details.keys())[0]
                    packages_dict = package_details[package_manager]
                    package_names = packages_dict.get('packages', [])
                    
                    if not package_names:
                        continue

                    all_packages_exist = True
                    missing_packages = []
                    detected_info_for_context = None
                    
                    for original_package_name in package_names:
                        exists = False
                        package_name = original_package_name

                        if package_manager == 'pip':
                            # For pip packages, check pypi
                            exists = _find_package(config, package_name, 'pypi', '', '')
                        else:
                            package_name = _apply_name_replacements(config, package_name, platform, version)

                            # Check all supported architectures
                            for os_arch in config['supported_arches'].get(platform, []):
                                res = _find_package(config, package_name, platform, version, os_arch)
                                if res:
                                    exists = True
                                    break

                            # Get detected info for context
                            if not detected_info_for_context:
                                detected_info_for_context = aggregated_dist_table.get_first_package_info(platform, version)

                        if not exists:
                            all_packages_exist = False
                            missing_packages.append(original_package_name)

                    for package_name in missing_packages:
                        detected_package = ''
                        if detected_info_for_context:
                            detected_package = detected_info_for_context.bin_name

                        _add_defect(defects, 'B.1a', entry_to_check, {
                            'reason': 'not_found',
                            'platform': platform,
                            'version': version,
                            'package_name': package_name,
                            'detected_package': detected_package
                        })

                    if all_packages_exist:
                        if package_manager == 'pip':
                            os_package_info = aggregated_dist_table.get_first_package_info(platform, version)
                            if os_package_info:
                                for package_name in package_names:
                                    _add_defect(defects, 'B.2a', entry_to_check, {
                                        'current_manager': 'pip',
                                        'suggested_platform': platform,
                                        'suggested_version': version,
                                        'suggested_package': os_package_info.bin_name
                                    })
                                break
                        else:
                            expected_packages = aggregated_dist_table.get_packages_info(platform, version)
                            if expected_packages:
                                entry_package_names = set()
                                for package_name in package_names:
                                    transformed_name = normalize_special_package_name(_apply_name_replacements(config, package_name, platform, version))
                                    entry_package_names.add(transformed_name)

                                expected_package_names = {
                                    _normalize_for_platform_package_compare(pkg.bin_name, platform)
                                    for pkg in expected_packages
                                }

                                entry_package_names = {
                                    _normalize_for_platform_package_compare(name, platform)
                                    for name in entry_package_names
                                }

                                if entry_package_names != expected_package_names:
                                    first_package_name = _apply_name_replacements(config, package_names[0], platform, version)
                                    first_expected_package = next(iter(expected_packages)).bin_name

                                    _add_defect(defects, 'B.1b', entry_to_check, {
                                        'reason': 'differs',
                                        'platform': platform,
                                        'version': version,
                                        'package_name': first_package_name,
                                        'expected_package': first_expected_package
                                    })

    return defects


# ============================================================================
# DEFECT FORMATTING
# ============================================================================

def format_defects_to_markdown(defects: dict, entry_key: str) -> str:
    """
    Format defects dictionary to human-friendly markdown format.

    Args:
        defects: Dict from check_entry_all_defects with structure
                 {'A.1': [...], 'A.2': [...], 'B.1a': [...], ...}
        entry_key: The rosdep entry key

    Returns:
        Markdown formatted string
    """
    lines = [f"\n### Entry: `{entry_key}`\n"]

    if not defects or all(len(v or []) == 0 for v in defects.values()):
        lines.append("- No issues detected\n")
        return "\n".join(lines)

    type_descriptions = {
        'A.1': 'Entry not found in database',
        'A.2': 'Missing platform/version in entry',
        'B.1a': 'Package not found in repository',
        'B.1b': 'Package name differs from expected',
        'B.2a': 'Using pip when OS repo has it',
        'B.2b': 'Distribution entry exists in OS repo',
    }

    for defect_type in sorted(defects.keys()):
        items = defects.get(defect_type, []) or []
        if not items:
            continue

        title = type_descriptions.get(defect_type, defect_type)
        lines.append(f"- **Type {defect_type}**: {title}")

        for item in items:
            desc = item.get('description', '')
            lines.append(f"  - {desc}")
        lines.append("")

    return "\n".join(lines)


# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit one rosdep key against configured package repositories."
    )
    parser.add_argument("key", help="Rosdep key to audit")
    parser.add_argument(
        "--rosdep-file",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rosdep",
            "python.yaml",
        ),
        help="Rosdep YAML containing the key (defaults to rosdep/python.yaml)",
    )
    parser.add_argument(
        "--config",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "configs",
            "config_usefulness.yaml",
        ),
        help="Configuration YAML (defaults to config_usefulness.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    database = build_rosdep_database(config, load_yaml_file(args.rosdep_file))
    defects = check_entry_all_defects(config, database, args.key)
    print(format_defects_to_markdown(defects, args.key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

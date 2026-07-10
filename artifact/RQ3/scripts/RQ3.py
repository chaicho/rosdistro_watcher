#!/usr/bin/env python3
"""
RQ3 Analysis - Quantify automation barriers in cross-repository package mapping.
"""

import os
import sys
from pathlib import Path
from collections import defaultdict

# TOOL_PATH points to the main repo root for importing rosdep_auditor modules
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(TOOL_PATH))

from rosdep_auditor.distro_parser import RosdepDatabase
from rosdep_auditor.config import load_config
from rosdep_auditor.problem_detector import _collect_local_rosdep_yaml_contents, build_rosdep_database
from rosdep_auditor.tools.name_pattern import clean_package_name, infer_roles
import re


def normalize_type_affix(package_name: str) -> str:
    """Normalize package names from special repositories like gentoo and openembedded."""
    pkg = package_name.lower()
    
    if pkg.startswith("'") and pkg.endswith("'"):
        pkg = pkg[1:-1]

    if '@' in pkg:
        pkg = pkg.split('@')[0]

    if '/' in pkg:
        pkg = pkg.split('/')[-1]

    return pkg


def fully_normalize_package_name(package_name: str) -> str:
    """Fully normalize package name by removing all known prefixes/suffixes and versions."""
    pkg = normalize_type_affix(package_name)
    pkg = remove_evolution_patterns(pkg)

    # Then apply full cleaning from name_pattern module
    pkg = clean_package_name(pkg, remove_version=True, remove_symbols=False, distribution_specific=False)

    return pkg


def load_database(rosdep_dir: str = None, config_path: str = None) -> RosdepDatabase:
    """Load the local rosdep database."""
    if rosdep_dir is None:
        rosdep_dir = str(TOOL_PATH / 'rosdep')
    if config_path is None:
        config_path = str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_wide_versions.yaml')

    config = load_config(config_path)
    file_contents = _collect_local_rosdep_yaml_contents(rosdep_dir, include_distribution=False)
    return build_rosdep_database(config, file_contents)


def normalize_without_affixes(package_name: str) -> str:
    """
    Normalize package name by removing affixes (prefixes/suffixes) but NOT versions or symbols.
    This is used to detect affixation-based naming inconsistencies.
    """
    return clean_package_name(package_name, remove_version=False, remove_symbols=False, distribution_specific=False)


def remove_evolution_patterns(package_name: str) -> str:
    """Remove evolution patterns (version numbers and special version markers) from package name."""
    result = package_name.lower()
    for pattern in ['t64', '0git', '1git', '2git', 'rc', 'alpha', 'beta']:
        result = result.replace(pattern, '')
    result = result.replace('-*', '')
    result = clean_package_name(result, remove_version=True, remove_symbols=False, distribution_specific=False)
    result = result.replace('-*', '')

    return result


def categorize_naming_inconsistency(entry_name: str, entry, db: RosdepDatabase) -> list:
    """
    Categorize the root cause(s) of naming inconsistency for an entry.

    Returns: List of categories - can include multiple ['affixation', 'evolution', 'policy']
    """
    try:
        distribution_table = entry.to_distribution_table()

        # Collect all packages, requiring exactly one package per repo_key
        all_packages = []
        for repo_key, package_set in distribution_table.items():
            assert len(package_set) == 1
            pkg_count = 0
            selected_pkg = None
            for pkg in package_set:
                if pkg.bin_name:
                    pkg_count += 1
                    selected_pkg = pkg

            if pkg_count != 1:
                return ['unknown']

            platform = repo_key.split('_')[0] if '_' in repo_key else repo_key
            minimal_norm = normalize_type_affix(selected_pkg.bin_name)
            without_affixes = normalize_without_affixes(minimal_norm)
            all_packages.append((platform, selected_pkg.bin_name, minimal_norm, without_affixes))

        if not all_packages:
            return ['unknown']

        # Get all package names at different normalization levels
        all_minimally_normalized = [minimal for _, _, minimal, _ in all_packages]
        all_without_affixes = [without for _, _, _, without in all_packages]

        categories = []

        unique_minimal = set(all_minimally_normalized)
        unique_without_affixes = set(all_without_affixes)

        if len(unique_without_affixes) < len(unique_minimal):
            categories.append('affixation')

        without_evolution = []
        for pkg in all_without_affixes:
            if pkg:
                without_evo = remove_evolution_patterns(pkg)
                without_evolution.append(without_evo)

        unique_without_evolution = set(without_evolution)

        if len(unique_without_evolution) < len(unique_without_affixes):
            categories.append('evolution')

        fully_normalized = set()
        for pkg in without_evolution:
            norm = fully_normalize_package_name(pkg)
            if norm:
                fully_normalized.add(norm)

        if len(fully_normalized) > 1:
            categories.append('policy')

        if not categories:
            return ['unknown']

        return categories

    except Exception as e:
        return ['unknown']


def analyze_rq3(db: RosdepDatabase) -> dict:
    """
    Analyze naming inconsistencies for RQ3.

    Returns statistics:
    - Total entries analyzed
    - Entries with inconsistent naming (after normalization)
    - Breakdown by root cause (affixation, evolution, policy)
    """
    all_entries = db.get_all_entries(include_distribution_entries=False)
    total_analyzed = 0
    inconsistent_entries = []
    categories = defaultdict(list)

    for entry_name in all_entries:
        entry = db.get_entry(entry_name)
        if not entry:
            continue

        try:
            distribution_table = entry.to_distribution_table()
            platforms = set()

            # First, verify each repo_key has exactly one package
            valid_entry = True
            for repo_key, package_set in distribution_table.items():
                platform = repo_key.split('_')[0] if '_' in repo_key else repo_key
                platforms.add(platform)

                # Count packages with bin_name in this repo_key
                pkg_count = sum(1 for pkg in package_set if pkg.bin_name)
                if pkg_count != 1:
                    # Skip entries where any repo_key has != 1 package
                    if pkg_count == 0:
                      assert 0
                    valid_entry = False
                    break

            if not valid_entry:
                continue

            # Only analyze entries that span multiple platforms
            if len(platforms) < 2:
                continue

            total_analyzed += 1

            # Paper's "normalization" (Step 3): Only remove Gentoo/OpenEmbedded special formats
            # (category prefixes like "dev-python/", variables like "${PYTHON_PN}")
            # NOT general prefixes like python3-, lib, -dev
            minimally_normalized_packages = set()

            for repo_key, package_set in distribution_table.items():
                for pkg in package_set:
                    if pkg.bin_name:
                        norm = normalize_type_affix(pkg.bin_name)
                        if norm:
                            minimally_normalized_packages.add(norm)

            is_inconsistent = len(minimally_normalized_packages) > 1

            if is_inconsistent:
                inconsistent_entries.append(entry_name)
                cats = categorize_naming_inconsistency(entry_name, entry, db)
                for cat in cats:
                    categories[cat].append(entry_name)

        except Exception:
            continue

    # Calculate statistics (entries can belong to multiple categories)
    total_inconsistent = len(inconsistent_entries)
    affixation_count = len(categories['affixation'])
    evolution_count = len(categories['evolution'])
    policy_count = len(categories['policy'])

    return {
        'total_analyzed': total_analyzed,
        'total_inconsistent': total_inconsistent,
        'consistent_after_normalization': total_analyzed - total_inconsistent,
        'inconsistency_rate': (total_inconsistent / total_analyzed * 100) if total_analyzed > 0 else 0,
        'affixation_count': affixation_count,
        'evolution_count': evolution_count,
        'policy_count': policy_count,
        'unknown_count': len(categories['unknown']),
        'inconsistent_entries': inconsistent_entries,
        'categories': dict(categories),
    }


def print_rq3_summary(stats: dict):
    """Print RQ3 statistics."""
    print("=" * 80)
    print("RQ3: AUTOMATION BARRIERS - SUMMARY")
    print("=" * 80)
    print()

    print(f"Total entries analyzed: {stats['total_analyzed']}")
    print(f"Entries with inconsistent naming (after normalization): {stats['total_inconsistent']}")
    print(f"Inconsistency rate: {stats['inconsistency_rate']:.1f}%")
    print()

    print("Root causes of naming inconsistencies:")
    print(f"  1. Divergent Affixation Rules: {stats['affixation_count']}")
    print(f"  2. Repository Evolution: {stats['evolution_count']}")
    print(f"  3. Differing Packaging Policies: {stats['policy_count']}")
    print(f"  4. Unknown/Other: {stats['unknown_count']}")
    print()

    # Verify counts add up (entries can belong to multiple categories)
    total_categorized = stats['affixation_count'] + stats['evolution_count'] + stats['policy_count'] + stats['unknown_count']
    print(f"Total category assignments: {total_categorized}")
    print(f"  (Entries can belong to multiple categories, so this exceeds {stats['total_inconsistent']})")

    # Calculate unique entries categorized (union of all categories)
    all_categorized = set()
    for cat in ['affixation', 'evolution', 'policy', 'unknown']:
        all_categorized.update(stats['categories'].get(cat, []))
    print(f"Unique entries categorized: {len(all_categorized)}/{stats['total_inconsistent']}")

    uncategorized = stats['total_inconsistent'] - len(all_categorized)
    if uncategorized > 0:
        print(f"  Warning: {uncategorized} entries uncategorized")
    elif uncategorized < 0:
        print(f"  Note: Some entries counted multiple times (overlap in categories)")


def save_detailed_results(stats: dict, db: RosdepDatabase, output_dir: str = None):
    """Save detailed results for manual inspection and category adjustment."""
    if output_dir is None:
        output_dir = str(TOOL_PATH / 'artifact' / 'RQ3' / 'data')

    os.makedirs(output_dir, exist_ok=True)

    # Save summary statistics
    summary_path = os.path.join(output_dir, "rq3_summary.txt")
    with open(summary_path, 'w') as f:
        f.write("RQ3 Analysis Summary\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total entries analyzed: {stats['total_analyzed']}\n")
        f.write(f"Entries with inconsistent naming: {stats['total_inconsistent']}\n")
        f.write(f"Inconsistency rate: {stats['inconsistency_rate']:.1f}%\n\n")
        f.write("Root causes:\n")
        f.write(f"  - Divergent Affixation Rules: {stats['affixation_count']}\n")
        f.write(f"  - Repository Evolution: {stats['evolution_count']}\n")
        f.write(f"  - Differing Packaging Policies: {stats['policy_count']}\n")
        f.write(f"  - Unknown: {stats['unknown_count']}\n")

    # Save all inconsistent entries summary file
    all_inconsistent_path = os.path.join(output_dir, "all_inconsistent_entries.txt")
    with open(all_inconsistent_path, 'w') as f:
        f.write("All Inconsistent Naming Entries\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total: {stats['total_inconsistent']}\n")
        f.write(f"Categories: affixation={stats['affixation_count']}, evolution={stats['evolution_count']}, ")
        f.write(f"policy={stats['policy_count']}, unknown={stats['unknown_count']}\n\n")

        # Build entry_categories mapping
        entry_categories = {}
        for cat, entries in stats['categories'].items():
            for entry in entries:
                if entry not in entry_categories:
                    entry_categories[entry] = []
                entry_categories[entry].append(cat)

        for entry_name in sorted(stats['inconsistent_entries']):
            cats = entry_categories.get(entry_name, [])
            f.write(f"{entry_name} | categories: {','.join(sorted(cats))}\n")

    # Load no_overlap_pairs.csv and analyze overlap
    csv_path = os.path.join(output_dir, "no_overlap_pairs.csv")
    csv_entries = set()
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as f:
            next(f)  # Skip header
            for line in f:
                if line.strip():
                    entry_name = line.split(',')[0]
                    csv_entries.add(entry_name)

        # Find overlap
        inconsistent_set = set(stats['inconsistent_entries'])
        overlap = inconsistent_set & csv_entries

        # Save overlap analysis
        overlap_summary_path = os.path.join(output_dir, "overlap_summary.txt")
        with open(overlap_summary_path, 'w') as f:
            f.write("Naming inconsistencies and filelist inconsistencies\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Inconsistent entries in RQ3: {len(inconsistent_set)}\n")
            f.write(f"Entries in no_overlap_pairs.csv: {len(csv_entries)}\n")
            f.write(f"Overlap (entries in both): {len(overlap)}\n")
            f.write(f"Percentage of inconsistent entries in CSV: {len(overlap) / len(inconsistent_set) * 100:.2f}%\n")
            f.write(f"Percentage of CSV entries that are inconsistent: {len(overlap) / len(csv_entries) * 100:.2f}%\n\n")

            # Breakdown by category
            f.write("Breakdown by category:\n")
            for cat in ['affixation', 'evolution', 'policy', 'unknown']:
                cat_entries = set(stats['categories'].get(cat, []))
                cat_in_csv = cat_entries & csv_entries
                pct = len(cat_in_csv)/len(cat_entries)*100 if cat_entries else 0
                f.write(f"  {cat.upper()}: {len(cat_entries)} total, {len(cat_in_csv)} in CSV ({pct:.1f}%)\n")

    # Save categorized entries with detailed package information
    for category, entries in stats['categories'].items():
        if not entries:
            continue

        category_path = os.path.join(output_dir, f"{category}_entries.txt")
        with open(category_path, 'w') as f:
            f.write(f"Category: {category.upper()}\n")
            f.write(f"Count: {len(entries)}\n")
            f.write("=" * 80 + "\n\n")

            for entry_name in sorted(entries):
                entry = db.get_entry(entry_name)
                if not entry:
                    continue

                f.write(f"Entry: {entry_name}\n")
                f.write("-" * 80 + "\n")

                # Get all packages for this entry
                try:
                    distribution_table = entry.to_distribution_table()

                    # Group by platform
                    platform_packages = defaultdict(list)
                    for repo_key, package_set in distribution_table.items():
                        platform = repo_key.split('_')[0] if '_' in repo_key else repo_key
                        for pkg in package_set:
                            if pkg.bin_name:
                                # Layer 1: Minimal normalization (remove repo-specific formats only)
                                minimal_norm = normalize_type_affix(pkg.bin_name)
                                # Layer 2: Remove affixes
                                without_affixes = normalize_without_affixes(minimal_norm)
                                # Layer 3: Remove evolution patterns
                                without_evolution = remove_evolution_patterns(without_affixes)
                                # Layer 4: Fully normalize (remove all symbols)
                                full_norm = fully_normalize_package_name(pkg.bin_name)
                                platform_packages[platform].append((
                                    pkg.bin_name, minimal_norm, without_affixes,
                                    without_evolution, full_norm
                                ))

                    # Write packages by platform (compact format)
                    for platform in sorted(platform_packages.keys()):
                        f.write(f"  {platform}:\n")
                        for orig, minimal, no_affix, no_evo, full in platform_packages[platform]:
                            f.write(f"    - {orig}\n")

                    all_minimal = set()
                    all_no_affix = set()
                    all_no_evo = set()
                    all_full = set()
                    for packages in platform_packages.values():
                        for _, minimal, no_affix, no_evo, full in packages:
                            all_minimal.add(minimal)
                            all_no_affix.add(no_affix)
                            all_no_evo.add(no_evo)
                            all_full.add(full)

                    f.write(f"\n  Summary:\n")
                    f.write(f"    Layer 1 (minimal): {len(all_minimal)} unique - {sorted(all_minimal)}\n")
                    f.write(f"    Layer 2 (no affixes): {len(all_no_affix)} unique - {sorted(all_no_affix)}\n")
                    f.write(f"    Layer 3 (no evolution): {len(all_no_evo)} unique - {sorted(all_no_evo)}\n")
                    f.write(f"    Layer 4 (full): {len(all_full)} unique - {sorted(all_full)}\n")

                except Exception as e:
                    f.write(f"  Error: {e}\n")

                f.write("\n" + "=" * 80 + "\n\n")

    print(f"\nDetailed results saved to: {output_dir}")


def main():
    """Run RQ3 analysis."""
    print("Loading rosdep database...")
    db = load_database()

    if db is None:
        print("Failed to load database")
        return

    print("Analyzing naming inconsistencies...")
    stats = analyze_rq3(db)

    print_rq3_summary(stats)
    save_detailed_results(stats, db)

    return stats


if __name__ == "__main__":
    main()

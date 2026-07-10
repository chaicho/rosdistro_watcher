#!/usr/bin/env python3
"""Build no_overlap_pairs.csv — pairwise Jaccard similarity for cross-platform entries."""

import os
import sys
import argparse
import csv
from pathlib import Path
from typing import Dict, List, Set

# Repository adapters read this setting at import time. Default to full paths
# before importing them, while still allowing an explicit caller override.
os.environ.setdefault("ROSDEP_USE_FULLPATH", "TRUE")

# TOOL_PATH points to the main repo root (consistent with RQ3.py)
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
RQ3_DATA_DIR = TOOL_PATH / "artifact" / "RQ3" / "data"

# Make repository-local modules importable when run as a script.
sys.path.insert(0, str(TOOL_PATH))

from rosdep_auditor.distro_parser import RosdepDatabase, RosdepDatabaseEntry
from rosdep_auditor.config import load_config
from rosdep_auditor.problem_detector import _collect_local_rosdep_yaml_contents, build_rosdep_database
from rosdep_auditor.existence_verifer import ExistenceVerifier


def calculate_jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Calculate Jaccard similarity: |intersection| / |union|."""
    if not set1 or not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union) if union else 0.0

def filter_cross_platform_entries(db: RosdepDatabase) -> List[str]:
    """
    Filter entries that are cross-platform with exactly 1 package per repo_key.
    """
    valid_entries = []
    all_entries = db.get_all_entries(include_distribution_entries=False)

    # The database may be backed by sets or mappings whose construction order
    # varies between processes. Always analyze entries in lexical order.
    for entry_name in sorted(all_entries):
        entry = db.get_entry(entry_name)
        if not entry:
            continue

        try:
            distribution_table = entry.to_distribution_table()
            platforms = set()
            valid_entry = True

            # Verify each repo_key has exactly one package
            for repo_key in sorted(distribution_table):
                package_set = distribution_table[repo_key]
                platform = repo_key.split('_')[0] if '_' in repo_key else repo_key
                platforms.add(platform)

                pkg_count = sum(1 for pkg in package_set if pkg.bin_name)
                if pkg_count != 1:
                    valid_entry = False
                    break

            if not valid_entry:
                continue

            # Only cross-platform entries
            if len(platforms) < 2:
                continue

            valid_entries.append(entry_name)
        except Exception:
            continue

    return valid_entries


def fetch_filelists_for_entry(
    entry: RosdepDatabaseEntry,
    config,
    use_fullpath: bool = True
) -> Dict[str, Dict[str, List[str]]]:
    """Fetch filelists for all packages in an entry using RepositoryCacheCollection."""
    filelists = {}
    verifier = ExistenceVerifier(config)

    try:
        distribution_table = entry.to_distribution_table()
        for repo_key in sorted(distribution_table):
            packages = distribution_table[repo_key]
            for package in sorted(
                packages,
                key=lambda pkg: (
                    pkg.bin_name or "",
                    pkg.repo_name or "",
                    pkg.repo_version or "",
                    pkg.arch or "",
                ),
            ):
                assert len(packages) == 1
                # Get default architecture from config if package.arch is empty
                os_arch = package.arch
                if not os_arch:
                    platform = package.repo_name
                    if platform in config.get('supported_arches', {}):
                        os_arch = config['supported_arches'][platform][0]
                    else:
                        os_arch = ""  # Fallback to empty if no default found
                        
                # Verify package exists in the repo
                verified_package = verifier.verify(
                    package.bin_name,
                    package.repo_name,
                    package.repo_version,
                    os_arch or ""
                )
                if verified_package is None or verified_package.filelist is None or len(verified_package.filelist) == 0:
                    # Package does not exist in repo, skip it
                    continue

                repo_key = verified_package.get_repo_key()
                if repo_key not in filelists:
                    filelists[repo_key] = {}
                filelists[repo_key][verified_package.bin_name] = verified_package.filelist

    except Exception as e:
        print(f"  Warning: Error fetching filelists: {e}")

    return filelists


def generate_pairwise_matrix(
    filelists: Dict[str, Dict[str, List[str]]],
    entry_name: str
) -> List[Dict]:
    """Generate pairwise similarity matrix for all platform combinations."""
    results = []

    # Extract single package per platform
    platform_files = {}
    for key in sorted(filelists):
        packages = filelists[key]
        for pkg in sorted(packages):
            files = packages[pkg]
            platform_files[key] = set(files)
            break

    platforms = sorted(platform_files)

    # Generate all pairs
    for i, p1 in enumerate(platforms):
        for p2 in platforms[i+1:]:
            files1 = platform_files[p1]
            files2 = platform_files[p2]

            jaccard = calculate_jaccard_similarity(files1, files2)

            results.append({
                'entry_name': entry_name,
                'platform1': p1,
                'platform2': p2,
                'jaccard_similarity': jaccard,
                'file_count1': len(files1),
                'file_count2': len(files2),
                'intersection': len(files1 & files2),
                'union': len(files1 | files2),
            })

    return results


def analyze_filelist_similarity(
    db: RosdepDatabase,
    config,
    use_fullpath: bool = True,
) -> Dict:
    """Find no-overlap pairs and write only the canonical RQ3 CSV."""

    # Filter entries
    print("Filtering cross-platform entries...")
    valid_entries = filter_cross_platform_entries(db)
    print(f"Found {len(valid_entries)} valid entries")
    print()

    # Pairwise results are kept only in memory until zero-overlap rows are selected.
    pairwise_results = []  # All pairs

    # Analyze each entry
    for i, entry_name in enumerate(valid_entries):
        if (i + 1) % 10 == 0:
            print(f"Progress: {i+1}/{len(valid_entries)}")

        entry = db.get_entry(entry_name)
        if not entry:
            continue

        # Fetch filelists
        filelists = fetch_filelists_for_entry(entry, config, use_fullpath)

        if not filelists or 'ubuntu_noble' not in filelists:
            continue

        # Pairwise matrix
        pairwise = generate_pairwise_matrix(filelists, entry_name)
        pairwise_results.extend(pairwise)

    print()
    print("Filelist retrieval completed")
    print()

    # Keep serialized output deterministic even if an upstream implementation
    # later changes its traversal order.
    pairwise_results.sort(
        key=lambda result: (
            result['entry_name'],
            result['platform1'],
            result['platform2'],
        )
    )

    # Save the sole output at the canonical path consumed by RQ3.py. Always
    # rewrite it, including the header-only case, so stale results cannot remain.
    no_overlap_pairs = [r for r in pairwise_results if r['jaccard_similarity'] == 0.0]
    no_overlap_csv = str(RQ3_DATA_DIR / 'no_overlap_pairs.csv')
    fieldnames = [
        'entry_name', 'platform1', 'platform2', 'jaccard_similarity',
        'file_count1', 'file_count2', 'intersection', 'union',
    ]
    with open(no_overlap_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(no_overlap_pairs)
    print(f"Saved {len(no_overlap_pairs)} no-overlap pairs to: {no_overlap_csv}")

    return {
        'no_overlap_csv': no_overlap_csv,
        'no_overlap_count': len(no_overlap_pairs),
    }


def main():
    """Build no_overlap_pairs.csv — the sole output consumed by RQ3.py."""
    use_fullpath = os.environ.get('ROSDEP_USE_FULLPATH', 'TRUE').upper() == 'TRUE'

    parser = argparse.ArgumentParser(
        description="Build no_overlap_pairs.csv (pairwise filelist Jaccard similarity)."
    )
    parser.add_argument(
        "--config",
        default=str(TOOL_PATH / "rosdep_auditor" / "configs" / "config_wide_versions.yaml"),
        help="Path to config YAML.",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("FILELIST SIMILARITY ANALYSIS")
    print("=" * 80)
    print()
    print(f"Filelist mode: {'Full paths' if use_fullpath else 'Component IDs'}")
    print()

    # Load database
    print("Loading rosdep database...")
    config = load_config(args.config)
    rosdep_dir = str(TOOL_PATH / 'rosdep')
    file_contents = _collect_local_rosdep_yaml_contents(rosdep_dir, include_distribution=False)
    db = build_rosdep_database(config, file_contents)
    print(f"Loaded {len(db.get_all_entries(include_distribution_entries=False))} rosdep entries")
    print()

    results = analyze_filelist_similarity(db, config, use_fullpath=use_fullpath)

    print()
    print("=" * 80)
    print("DONE")
    print("=" * 80)
    print()
    print(f"Output: {results['no_overlap_csv']} ({results['no_overlap_count']} rows)")
    return results


if __name__ == "__main__":
    main()

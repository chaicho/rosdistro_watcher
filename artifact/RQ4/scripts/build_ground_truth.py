#!/usr/bin/env python3
"""
Build and filter the RQ4 ground truth dataset.

PRs are discovered via GitHub Search API (ros/rosdistro, label:rosdep,
created after --start-date and merged before --end-date).

Usage:
  python build_ground_truth.py --build   # Build from API search
  python build_ground_truth.py --filter  # Filter existing dataset
  python build_ground_truth.py --all     # Build + filter
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests
import yaml

# Add project root to path for rosdep_auditor imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from rosdep_auditor.config import load_config
from rosdep_auditor.distro_parser import RosdepDatabase
from rosdep_auditor.pr_analyzer import (
    detect_edited_files,
    detect_lines,
    get_file_content,
    get_pr_details,
    get_pr_diff,
    headers,
    parse_github_pr_url,
)
from rosdep_auditor.tools.logger import set_log_level
from rosdep_auditor.tools.yaml import (
    AnnotatedSafeLoader,
    isolate_yaml_snippets_from_line_numbers,
)

# Default paths
ARTIFACT_DIR = _PROJECT_ROOT / "artifact" / "RQ4" / "data"
DEFAULT_OUTPUT = ARTIFACT_DIR / "ground_truth_dataset.yaml"
DEFAULT_FILTERED = ARTIFACT_DIR / "ground_truth_dataset_filtered.yaml"
CONFIG_PATH = "rosdep_auditor/configs/config_distribution_comparison.yaml"

# Time window for PRs
DEFAULT_START = "2025-05-01"
DEFAULT_END = "2025-09-30"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PR source: GitHub Search API
# ---------------------------------------------------------------------------

def search_merged_prs(start_date: str = DEFAULT_START,
                      end_date: str = DEFAULT_END) -> list:
    """
    Search GitHub for merged rosdep PRs in ros/rosdistro that were *created*
    after ``start_date`` and *merged* before ``end_date``.  Returns a list of
    PR detail dicts.
    """
    query = (
        f"repo:ros/rosdistro is:pr is:merged label:rosdep "
        f"merged:{start_date}..{end_date}"
    )
    pr_details_list = []
    page = 1

    while page <= 10:
        url = (
            f"https://api.github.com/search/issues"
            f"?q={query}&sort=created&order=desc&per_page=100&page={page}"
        )
        print(f"Fetching page {page}...")
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        time.sleep(2)  # Search API rate limit

        if not items:
            break

        for pr in items:
            # Filter by creation date (in addition to merge-date filter above)
            created_at = pr.get("created_at", "")
            if created_at < start_date:
                print(f"  Skipping #{pr['number']} (created {created_at} < {start_date})")
                continue

            pr_number = pr["number"]
            try:
                details = get_pr_details("ros", "rosdistro", str(pr_number))
                pr_details_list.append(details)
                print(f"  #{pr_number} {pr.get('title', '')[:60]}")
            except Exception as e:
                print(f"  Error fetching PR #{pr_number}: {e}")
                continue

        if len(items) < 100:
            break
        page += 1

    print(f"Found {len(pr_details_list)} merged PRs via search API")
    return pr_details_list


# ---------------------------------------------------------------------------
# Diff → modified keys
# ---------------------------------------------------------------------------

def get_modified_keys(pr_details: dict) -> tuple:
    """
    Analyse a PR diff to extract the set of modified rosdep keys.

    Returns ``(modified_keys: set[str], content_before: dict, content_after: dict)``.
    """
    raw_diff = get_pr_diff(pr_details["diff_url"])
    owner, pr_repo, pr_number = parse_github_pr_url(pr_details["html_url"])
    modified_keys = set()

    added_lines, removed_lines = detect_lines(raw_diff)
    edited_files = detect_edited_files(raw_diff, keep_orginal_file=True)
    print(f"  Edited files: {list(edited_files.keys())}")

    file_content_before = {}
    file_content_after = {}

    for file_path in edited_files:
        file_content_before[file_path] = get_file_content(
            owner, pr_repo, pr_details["base"]["sha"], file_path
        )
        file_content_after[file_path] = get_file_content(
            owner, pr_repo, pr_details["head"]["sha"], file_path
        )
        if file_content_before[file_path] is None or file_content_after[file_path] is None:
            print(f"  Failed to get file content for {file_path}")
            continue

        isolated_data_before = {}
        isolated_data_after = {}

        yaml_before = yaml.load(file_content_before[file_path], Loader=AnnotatedSafeLoader)
        yaml_after = yaml.load(file_content_after[file_path], Loader=AnnotatedSafeLoader)

        if file_path in removed_lines:
            isolated_data_before = isolate_yaml_snippets_from_line_numbers(
                yaml_before, removed_lines[file_path]
            )
        if file_path in added_lines:
            isolated_data_after = isolate_yaml_snippets_from_line_numbers(
                yaml_after, added_lines[file_path]
            )

        if "distribution.yaml" in file_path:
            for isolated in (isolated_data_before, isolated_data_after):
                if "repositories" in isolated:
                    for repo, repo_data in isolated["repositories"].items():
                        if "release" in repo_data and "packages" in repo_data["release"]:
                            for pkg in repo_data["release"]["packages"]:
                                modified_keys.add(pkg.replace("_", "-"))
                        else:
                            modified_keys.add(repo.replace("_", "-"))
        else:
            modified_keys.update(isolated_data_before.keys())
            modified_keys.update(isolated_data_after.keys())

    # Convert AnnotatedStr to plain strings
    modified_keys = {str(k) for k in modified_keys}

    return modified_keys, file_content_before, file_content_after


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_ground_truth(pr_list: list,
                       output_path: str = None) -> dict:
    """
    Build the ground-truth rosdep dataset from a list of PR detail dicts.

    For each PR the diff is analysed to find modified rosdep keys; the
    *post-PR* content of those keys is merged into a flat YAML dictionary.
    Always starts from scratch.

    Parameters
    ----------
    pr_list : list[dict]
        PR detail dicts (from GitHub API).
    output_path : str
        Where to write ``ground_truth_dataset.yaml``.

    Returns
    -------
    dict
        The complete ground-truth data.
    """
    if output_path is None:
        output_path = str(DEFAULT_OUTPUT)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    ground_truth_data = {}

    set_log_level(logging.INFO)
    total_processed = 0

    for count, pr_details in enumerate(pr_list, 1):
        pr_url = pr_details.get("html_url", "")
        pr_number = pr_details.get("number", "?")

        try:
            print(f"\n{'=' * 80}")
            print(f"Processing PR {count}/{len(pr_list)}: {pr_url}")

            # Extract modified keys
            modified_keys, _content_before, content_after = get_modified_keys(pr_details)

            # Filter out keys starting with "python-"
            modified_keys = {k for k in modified_keys if not k.startswith("python-")}

            if not modified_keys:
                print(f"  No modified keys found (after filtering python- keys)")
                continue

            print(f"  Modified keys: {', '.join(sorted(modified_keys))}")

            # Merge rosdep data from post-PR file content
            for file_path, file_content in content_after.items():
                if file_content is None:
                    continue
                if "distribution.yaml" in file_path:
                    print(f"  Skipping distribution.yaml (not rosdep format): {file_path}")
                    continue

                try:
                    yaml_data = yaml.safe_load(file_content)
                    if yaml_data is None:
                        continue

                    for key in modified_keys:
                        if key in yaml_data:
                            ground_truth_data[key] = yaml_data[key]
                            print(f"    Added rosdep key '{key}' from {file_path}")
                except Exception as e:
                    print(f"  Error parsing YAML content for {file_path}: {e}")
                    continue

            total_processed += 1

            # Write after every PR so work is not lost on interruption
            with open(output_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    ground_truth_data, f,
                    default_flow_style=None, allow_unicode=True, sort_keys=True,
                )

            print(f"  Dataset updated.  Total keys: {len(ground_truth_data)}, "
                  f"PRs processed: {total_processed}")

        except Exception as e:
            print(f"  Error processing PR {pr_url}: {e}")
            continue

        time.sleep(1)  # Rate limiting

    print(f"\n{'=' * 80}")
    print(f"Build complete!")
    print(f"  PRs processed: {total_processed}")
    print(f"  Total rosdep keys: {len(ground_truth_data)}")
    print(f"  Output: {output_path}")

    return ground_truth_data


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def filter_ground_truth(input_path: str = None,
                        output_path: str = None,
                        min_repos: int = 2) -> dict:
    """
    Filter ground truth to entries that satisfy:

    * mapped to **ubuntu_noble**, AND
    * contain at least *min_repos* distinct repository platforms.

    Returns the filtered dict.
    """
    if input_path is None:
        input_path = str(DEFAULT_OUTPUT)
    if output_path is None:
        output_path = str(DEFAULT_FILTERED)

    # Load config (needed by RosdepDatabase for platform normalisation)
    config = load_config(CONFIG_PATH)

    with open(input_path, "r", encoding="utf-8") as f:
        ground_truth_data = yaml.safe_load(f) or {}

    print(f"Total entries in ground truth: {len(ground_truth_data)}")

    # Build RosdepDatabase from ground truth (for to_distribution_table)
    database = RosdepDatabase(config)
    database.load_yaml(yaml.dump(ground_truth_data))

    filtered_data = {}
    removed = []

    for entry_name, entry_data in ground_truth_data.items():
        entry = database.get_entry(entry_name)
        if entry is None:
            print(f"  Warning: Entry '{entry_name}' not found in database")
            removed.append((entry_name, 0, [], "entry not in DB"))
            continue

        dist_table = entry.to_distribution_table()
        repo_keys = list(dist_table.keys())
        has_noble = "ubuntu_noble" in repo_keys
        enough_repos = len(repo_keys) >= min_repos

        if has_noble and enough_repos:
            filtered_data[entry_name] = entry_data
        else:
            reason_parts = []
            if not has_noble:
                reason_parts.append("no ubuntu_noble")
            if not enough_repos:
                reason_parts.append(f"only {len(repo_keys)} repo(s), need >= {min_repos}")
            removed.append((entry_name, len(repo_keys), repo_keys, ", ".join(reason_parts)))

    # Print statistics
    print(f"\nFiltering results (min_repos={min_repos}, require ubuntu_noble):")
    print(f"  Kept:   {len(filtered_data)}")
    print(f"  Removed: {len(removed)}")

    if removed:
        print(f"\n--- Removed entries ---")
        for name, count, repos, reason in removed:
            print(f"  {name}: {count} repo(s) -> {repos}  [{reason}]")

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            filtered_data, f,
            default_flow_style=None, allow_unicode=True, sort_keys=True,
        )
    print(f"\nFiltered dataset saved to: {output_path}")

    return filtered_data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build and filter RQ4 ground truth dataset"
    )
    parser.add_argument(
        "--build", action="store_true",
        help="Build ground_truth_dataset.yaml from PRs (GitHub API)",
    )
    parser.add_argument(
        "--filter", action="store_true",
        help="Filter existing dataset -> ground_truth_dataset_filtered.yaml",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Build + filter in one go",
    )
    parser.add_argument(
        "--start-date", type=str, default=DEFAULT_START,
        help=f"Start date for PR search (default: {DEFAULT_START})",
    )
    parser.add_argument(
        "--end-date", type=str, default=DEFAULT_END,
        help=f"End date for PR search (default: {DEFAULT_END})",
    )
    parser.add_argument(
        "--input", type=str, default=None,
        help="Path to input ground_truth_dataset.yaml (for --filter)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Path to output file",
    )
    parser.add_argument(
        "--min-repos", type=int, default=2,
        help="Minimum repos for filtering (default: 2)",
    )

    args = parser.parse_args()

    # --all implies --build and --filter
    do_build = args.build or args.all
    do_filter = args.filter or args.all

    if not do_build and not do_filter:
        parser.print_help()
        print("\nError: specify --build, --filter, or --all")
        sys.exit(1)

    # ---- Build ----
    if do_build:
        pr_list = search_merged_prs(args.start_date, args.end_date)

        if not pr_list:
            print("No PRs found. Aborting build.")
            sys.exit(1)

        output_path = args.output or str(DEFAULT_OUTPUT)
        build_ground_truth(pr_list, output_path=output_path)

    # ---- Filter ----
    if do_filter:
        input_path = args.input or str(DEFAULT_OUTPUT)
        output_path = args.output or str(DEFAULT_FILTERED)
        filter_ground_truth(
            input_path=input_path,
            output_path=output_path,
            min_repos=args.min_repos,
        )


if __name__ == "__main__":
    main()

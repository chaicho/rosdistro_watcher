#!/usr/bin/env python3

import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from urllib.parse import urlparse
import requests

# TOOL_PATH points to the main repo root for importing rosdep_auditor modules
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(TOOL_PATH))

from rosdep_auditor.pr_analyzer import (
    parse_github_pr_url, get_file_content, detect_lines, detect_edited_files, get_pr_details
)
from rosdep_auditor.tools.yaml import AnnotatedSafeLoader, isolate_yaml_snippets_from_line_numbers
from rosdep_auditor.distro_parser import RosdepDatabase
RQ5_PATH = TOOL_PATH / 'artifact' / 'RQ5' / 'scripts'
sys.path.insert(0, str(RQ5_PATH))
from run_experiments import get_modified_keys
from typing import Dict, Set

_defect_type_dict = {}
_all_rows = []
# GitHub token - use environment variable or prompt for it
github_token = os.environ.get('GITHUB_TOKEN', '')
# pr_manual_analyzed_final.csv is in RQ1/data (same source data)
INPUT_FILE = str(TOOL_PATH / 'artifact' / 'RQ1' / 'data' / 'pr_manual_analyzed_final.csv')
# RQ2 data files
DATA_DIR = TOOL_PATH / 'artifact' / 'RQ2' / 'data'
COMMITS_FILE = str(DATA_DIR / 'commits_after_review.csv')
MODIFIED_KEYS_FILE = str(DATA_DIR / 'pr_modified_keys.csv')
COMMENTS_FILE = str(DATA_DIR / 'comments_in_prs.csv')
CHAINED_MODIFICATIONS_FILE = str(DATA_DIR / 'pr_chained_modification.csv')
KEY_PACKAGES_FILE = str(DATA_DIR / 'key_packages.csv')

def load_data(file_path: str) -> dict:
    """Load data file into dict keyed by pr_url with parsed values."""
    import json
    result = {}
    if not os.path.exists(file_path):
        return result
    with open(file_path, 'r', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if file_path == COMMITS_FILE:
                result[r['pr_url']] = int(r['commits_after_review'])
            elif file_path == MODIFIED_KEYS_FILE:
                v = r['modified_keys']
                result[r['pr_url']] = v.split(', ') if v and v != 'ERROR' else []
            elif file_path == COMMENTS_FILE:
                result[r['pr_url']] = json.loads(r['comments_cnt_dict'])
    return result


def github_api_get(url: str, timeout: int = 30, max_retries: int = 3,
                   return_text: bool = False) -> dict | list | str:
    """
    Generic GitHub REST API GET request function with retry logic.

    Args:
        url: Full GitHub API URL or diff URL
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        return_text: If True, return raw text instead of JSON

    Returns:
        JSON data from API or raw text

    Raises:
        ValueError: If github_token is not set
        requests.HTTPError: If the request fails
    """
    if not github_token:
        raise ValueError("Global github_token is not set")
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            time.sleep(1)
            return r.text if return_text else r.json()
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 3  # 3s, 6s, 9s
                print(f"  Retry {attempt+1}/{max_retries} after {wait_time}s: {e}")
                time.sleep(wait_time)
    
    raise last_error or Exception(f"Failed after {max_retries} attempts: {url}")


def parse_input_file():
    """
    Parse the input csv and return the data as a dict grouped by classification type,
    plus a list of all rows as dicts.
    
    Returns:
        tuple: (type_dict, all_rows)
            - type_dict: {type: [row_data_list, ...]}
            - all_rows: [row_dict, ...]
    """
    global _defect_type_dict, _all_rows
    
    if _defect_type_dict:
        return _defect_type_dict, _all_rows
      
    result = {}
    all_rows = []
    
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        
        for row in reader:
            # Filter: keep only merged PRs
            if row.get('status') != 'merged':
                continue

            # Filter: classification_types must be valid
            types_str = row.get('classification_types', '')
            if not types_str or types_str in ('No classification found', 'ERROR'):
                continue
            
            types_set = {t.strip() for t in types_str.split(',')}
            all_rows.append(dict(row))
            
            # Convert row to list (preserving header order)
            row_data = [row[h] for h in headers]
            
            # Add row to each type's list in the result dict
            for issue_type in types_set:
                if issue_type not in result:
                    result[issue_type] = []
                result[issue_type].append(row_data)    
  
    _defect_type_dict = result
    _all_rows = all_rows
    return result, all_rows

def analyze_time_durations():
    """
    Analyze the time durations of the PRs for each classification type.
    Calculates average time from created_at to closed_at for each type.
    """
    
    # Column indices: pr_url (3), status (5), created_at (8), closed_at (9)
    PR_URL_IDX = 3
    STATUS_IDX = 5
    CREATED_AT_IDX = 8
    CLOSED_AT_IDX = 9
    
    defect_type_dict, _ = parse_input_file()
    
    type_durations = {}
    all_prs = {}  # url -> duration (for deduplication)
    
    for defect_type, rows in defect_type_dict.items():
        durations = []
        for row in rows:
            pr_url = row[PR_URL_IDX]
            status = row[STATUS_IDX]
            created_at_str = row[CREATED_AT_IDX]
            closed_at_str = row[CLOSED_AT_IDX]
            
            # Only count merged PRs
            if status != 'merged':
                continue
            
            if not created_at_str or not closed_at_str:
                continue
                
            try:
                created_at = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ")
                closed_at = datetime.strptime(closed_at_str, "%Y-%m-%dT%H:%M:%SZ")
                duration_hours = (closed_at - created_at).total_seconds() / 3600
                durations.append(duration_hours)
                all_prs[pr_url] = duration_hours
            except ValueError:
                continue
        
        if durations:
            type_durations[defect_type] = {
                'count': len(durations),
                'avg_hours': sum(durations) / len(durations),
                'median_hours': median(durations)
            }
    
    # Print results
    print(f"=== Average time duration for all PRs to be merged ===")
    print(f"\n{'Type':<10} {'Count':<8} {'Avg (hours)':<14} {'Median (hours)':<14}")
    print("-" * 46)
    for defect_type in sorted(type_durations.keys()):
        stats = type_durations[defect_type]
        print(f"{defect_type:<10} {stats['count']:<8} {stats['avg_hours']:<14.2f} {stats['median_hours']:<14.2f}")
    
    # Total average and median (deduplicated by URL)
    total_avg = sum(all_prs.values()) / len(all_prs) if all_prs else 0
    total_median = median(all_prs.values()) if all_prs else 0
    print("-" * 46)
    print(f"{'Total':<10} {len(all_prs):<8} {total_avg:<14.2f} {total_median:<14.2f}")
    
    return type_durations


def count_commits_after_review_requested(pr_url: str) -> int:
    """
    Return the number of committed events after the first review_requested in the timeline.
    """
    owner, repo, pr_number = parse_github_pr_url(pr_url)
    base = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/timeline"

    seen_review_requested = False
    committed_count = 0

    page = 1
    while True:
        events = github_api_get(f"{base}?per_page=100&page={page}")
        if not events:
            break

        for ev in events:
            et = ev.get("event")
            if et == "review_requested":
                seen_review_requested = True
            elif seen_review_requested and et == "committed":
                committed_count += 1

        if len(events) < 100:
            break
        page += 1

    return committed_count


def analyze_commits_after_review(output_file: str, cross_check = True):
    """Analyze commits_after_review results."""
    # Load commits data
    pr_commits = {}
    with open(output_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            count = int(row['commits_after_review'])
            if count >= 0:
                pr_commits[row['pr_url']] = count
    
    counts = list(pr_commits.values())
    has_commits = [c for c in counts if c > 0]
    print(f"\n=== Commits After Review Analysis ===")
    print(f"Total PRs: {len(counts)}")
    print(f"PRs with commits after review: {len(has_commits)} ({100*len(has_commits)/len(counts):.1f}%)")
    print(f"Average commits: {sum(counts)/len(counts):.2f}")
    print(f"Median commits: {median(counts)}")
    print(f"Max commits: {max(counts)}")
    
    # Calculate average commits by defect type
    from collections import defaultdict
    _, all_rows = parse_input_file()
    
    type_commits = defaultdict(list)
    for row in all_rows:
        pr_url = row['pr_url']
        if pr_url not in pr_commits:
            continue
        types_str = row.get('classification_types', '')
        if not types_str or types_str in ('No classification found', 'ERROR'):
            continue
        types_set = {t.strip() for t in types_str.split(',')}
        for defect_type in types_set:
            type_commits[defect_type].append(pr_commits[pr_url])
    
    print(f"\nAverage commits after review by defect type:")
    print(f"{'Type':<10} {'Count':<8} {'Avg commits':<12}")
    print("-" * 30)
    for defect_type in sorted(type_commits.keys()):
        commits = type_commits[defect_type]
        avg = sum(commits) / len(commits)
        print(f"{defect_type:<10} {len(commits):<8} {avg:<12.2f}")
    
    # Output top 10 PRs with most commits after review
    top_10 = sorted(pr_commits.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"\n=== Top 10 PRs with Most Commits After Review ===")
    print(f"{'Rank':<6} {'Commits':<10} {'PR URL'}")
    print("-" * 80)
    for rank, (pr_url, commit_count) in enumerate(top_10, 1):
        print(f"{rank:<6} {commit_count:<10} {pr_url}")
    
    if cross_check:
        pass


def collect_commits_after_review(output_file: str):
    """Collect commit count after review_requested for all PRs and save results."""
    # If output_file exists and is non-empty, analyze directly
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        analyze_commits_after_review(output_file)
        return
    
    _, rows = parse_input_file()
    
    results = []
    for i, row in enumerate(rows):
        pr_url = row['pr_url']
        try:
            count = count_commits_after_review_requested(pr_url)
        except Exception as e:
            print(f"[{i+1}/{len(rows)}] Error {pr_url}: {e}")
            count = -1
        results.append({'pr_url': pr_url, 'commits_after_review': count})
        print(f"[{i+1}/{len(rows)}] {pr_url} -> {count}")
    
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['pr_url', 'commits_after_review'])
        writer.writeheader()
        writer.writerows(results)
    
    print(f"Saved to {output_file}")
    analyze_commits_after_review(output_file)

def _fetch_modified_keys(pr_url: str) -> str:
    """Get modified keys from PR using diff analysis"""
    owner, repo, pr_number = parse_github_pr_url(pr_url)
    pr_details = get_pr_details(owner, repo, pr_number)
    modified_keys, file_content_before, file_content_after = get_modified_keys(pr_details)
    return ', '.join(sorted(modified_keys)) if modified_keys else ''


def _extract_key_packages(modified_keys: Set[str], file_contents: Dict[str, str]) -> Dict[str, Dict[str, Set[tuple]]]:
    """
    Extract package names with context for each key from file contents. Config-free.
    
    Returns:
        Dict[key, Dict[package_name, Set[(platform, version, pkg_manager)]]]
    """
    if not modified_keys or not file_contents:
        return {}
    
    distribution_files = {fp for fp in file_contents if 'distribution' in fp}
    
    db = RosdepDatabase(config={})
    for file_path, content in file_contents.items():
        if content and file_path.endswith('.yaml') and 'distribution' not in file_path:
            db.load_yaml(content)
    
    result = {}
    for key in modified_keys:
        entry = db.get_entry(key)
        if entry:
            result[key] = entry.get_all_package_names()
        elif distribution_files:
            # distribution.yaml key: package name is the key itself, no specific context
            result[key] = {key: {('*', '*', '*')}}
    
    return result


def get_key_packages_for_pr(pr_url: str) -> tuple[Dict[str, Dict[str, Set[tuple]]], Dict[str, Dict[str, Set[tuple]]]]:
    """
    Get package names for each modified key in a PR (both before and after merge).
    Config-free: only parses YAML structure, no config dependency.
    
    Args:
        pr_url: GitHub PR URL
        
    Returns:
        Tuple of (before_packages, after_packages), each:
        Dict[key, Dict[package_name, Set[(platform, version, pkg_manager)]]]
    """
    import io
    import contextlib
    
    owner, repo, pr_number = parse_github_pr_url(pr_url)
    pr_details = get_pr_details(owner, repo, pr_number)
    
    # Suppress verbose output from get_modified_keys
    with contextlib.redirect_stdout(io.StringIO()):
        modified_keys, file_content_before, file_content_after = get_modified_keys(pr_details)
    
    if not modified_keys:
        return {}, {}
    
    before_packages = _extract_key_packages(modified_keys, file_content_before)
    after_packages = _extract_key_packages(modified_keys, file_content_after)
    
    return before_packages, after_packages


def _serialize_key_packages(data: Dict[str, Dict[str, Set[tuple]]]) -> str:
    """Serialize key_packages dict to JSON string."""
    import json
    # Convert: {key: {pkg: {(p,v,m), ...}}} -> {key: {pkg: [[p,v,m], ...]}}
    serializable = {
        key: {pkg: [list(ctx) for ctx in sorted(contexts)] for pkg, contexts in pkg_dict.items()}
        for key, pkg_dict in data.items()
    }
    return json.dumps(serializable)


def _deserialize_key_packages(json_str: str) -> Dict[str, Dict[str, Set[tuple]]]:
    """Deserialize JSON string back to key_packages dict."""
    import json
    if json_str == 'ERROR' or not json_str:
        return {}
    data = json.loads(json_str)
    # Convert: {key: {pkg: [[p,v,m], ...]}} -> {key: {pkg: {(p,v,m), ...}}}
    return {
        key: {pkg: {tuple(ctx) for ctx in contexts} for pkg, contexts in pkg_dict.items()}
        for key, pkg_dict in data.items()
    }


def collect_key_packages(output_file: str, retry_errors: bool = True):
    """Collect key -> package names mapping (before & after) for each PR and save to CSV."""
    import sys
    csv.field_size_limit(sys.maxsize)
    
    _, rows = parse_input_file()
    fieldnames = ['pr_url', 'key_packages_before', 'key_packages_after']
    
    # Load existing results for resuming
    existing = {}
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        with open(output_file, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if not retry_errors or row.get('key_packages_after') != 'ERROR':
                    existing[row['pr_url']] = row
        # print(f"Loaded {len(existing)} existing results")  # Verbose output removed
    
    results = []
    for i, row in enumerate(rows, 1):
        pr_url = row['pr_url']
        
        if pr_url in existing:
            results.append(existing[pr_url])
            continue

        # print(f"[{i}/{len(rows)}] {pr_url}")  # Verbose output removed
        try:
            before_pkgs, after_pkgs = get_key_packages_for_pr(pr_url)
            before_json = _serialize_key_packages(before_pkgs)
            after_json = _serialize_key_packages(after_pkgs)
        except Exception as e:
            print(f"  Error: {e}")
            before_json, after_json = 'ERROR', 'ERROR'
        
        results.append({'pr_url': pr_url, 'key_packages_before': before_json, 'key_packages_after': after_json})
        
        # Checkpoint save
        if i % 20 == 0:
            with open(output_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            # print(f"  [Checkpoint: {len(results)} PRs]")  # Verbose output removed

    # Final save,
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    # print(f"Saved {len(results)} results to {output_file}")  # Verbose output removed


def collect_pr_modified_keys(output_file: str, retry_errors: bool = True):
    """Collect modified keys for each PR and save to CSV.
    
    Args:
        output_file: Path to save results
        retry_errors: If True, retry PRs that previously had ERROR status
    """
    _, rows = parse_input_file()
    
    # Load existing results to support resuming
    existing_results = {}
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        with open(output_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                pr_url = row['pr_url']
                # Keep non-ERROR results, or all results if not retrying errors
                if not retry_errors or row['modified_keys'] != 'ERROR':
                    existing_results[pr_url] = row
        # print(f"Loaded {len(existing_results)} existing results (skipping non-ERROR entries)")  # Verbose output removed
    
    results = []
    error_count = 0
    skip_count = 0
    
    for i, row in enumerate(rows):
        pr_url = row['pr_url']
        created_at = row.get('created_at', '')
        closed_at = row.get('closed_at', '')
        
        # Skip if already have a valid (non-ERROR) result
        if pr_url in existing_results:
            results.append(existing_results[pr_url])
            skip_count += 1
            continue

        # print(f'Processing PR [{i+1}/{len(rows)}]: {pr_url}')  # Verbose output removed
        try:
            keys_str = _fetch_modified_keys(pr_url)
        except Exception as e:
            print(f"  Error: {e}")
            keys_str = 'ERROR'
            error_count += 1
        
        results.append({
            'pr_url': pr_url, 
            'modified_keys': keys_str, 
            'created_at': created_at, 
            'closed_at': closed_at
        })
        # print(f"  -> {keys_str[:60]}{'...' if len(keys_str) > 60 else ''}")  # Verbose output removed

        # Save periodically to avoid losing progress
        if (i + 1) % 50 == 0:
            with open(output_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['pr_url', 'modified_keys', 'created_at', 'closed_at'])
                writer.writeheader()
                writer.writerows(results)
            # print(f"  [Checkpoint saved: {len(results)} PRs]")  # Verbose output removed

    # Final save
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['pr_url', 'modified_keys', 'created_at', 'closed_at'])
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nSaved to {output_file}")
    print(f"Total: {len(results)}, Skipped: {skip_count}, Errors: {error_count}")
    analyze_iterative_keys_modification(output_file, verbose=True)


def analyze_iterative_keys_modification(output_file: str, verbose = False):
    """Analyze whether multiple PRs modify the same key and record the timeline."""
    from collections import defaultdict

    # key -> list of (closed_at, pr_url)
    key_timeline = defaultdict(list)

    with open(output_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pr_url = row['pr_url']
            keys_str = row['modified_keys']
            closed_at = row.get('closed_at', '')

            if keys_str == 'ERROR' or not keys_str:
                continue

            keys = [k.strip() for k in keys_str.split(',')]
            for key in keys:
                key_timeline[key].append((closed_at, pr_url))

    # Sort each key's timeline by date
    for key in key_timeline:
        key_timeline[key].sort(key=lambda x: x[0])

    # Filter to keys modified by multiple PRs
    multi_modified = {k: v for k, v in key_timeline.items() if len(v) > 1}

    print(f"\n=== Iterative Keys Modification Analysis ===")
    print(f"Total unique keys: {len(key_timeline)}")
    print(f"Keys modified by multiple PRs: {len(multi_modified)}")

    if multi_modified:
        # Verbose output for top 10 keys removed
        if verbose:
            pass  # Previously printed top 10 keys with most modifications
        
        # Calculate the PRs that were later modified (PRs not at the end of any key's timeline)
        later_modified_prs = set()
        for key, timeline in multi_modified.items():
            # All PRs except the last one were later modified
            for date, pr_url in timeline[:-1]:
                later_modified_prs.add(pr_url)
        print(f"PRs that were later modified: {len(later_modified_prs)}")
          
        # Calculate average modification times
        modification_counts = [len(v) for v in multi_modified.values()]
        avg_modifications = sum(modification_counts) / len(modification_counts)
        print(f"\nAverage modification times (for keys modified multiple times): {avg_modifications:.2f}")

        # Analyze adjacent modification intervals
        # (key, duration_days, pr1_url, pr1_date, pr2_url, pr2_date)
        all_intervals = []
        for key, timeline in multi_modified.items():
            for i in range(len(timeline) - 1):
                date1_str, pr1_url = timeline[i]
                date2_str, pr2_url = timeline[i + 1]
                if not date1_str or not date2_str:
                    continue
                try:
                    date1 = datetime.strptime(date1_str, "%Y-%m-%dT%H:%M:%SZ")
                    date2 = datetime.strptime(date2_str, "%Y-%m-%dT%H:%M:%SZ")
                    duration_days = (date2 - date1).days
                    all_intervals.append((key, duration_days, pr1_url, date1_str, pr2_url, date2_str))
                except ValueError:
                    continue
        
        print(f"\n=== Adjacent Modification Intervals Analysis ===")
        print(f"Total adjacent intervals: {len(all_intervals)}")
        
        if all_intervals:
            durations = [x[1] for x in all_intervals]
            print(f"Average duration (days): {sum(durations)/len(durations):.1f}")
            print(f"Median duration (days): {median(durations)}")
            print(f"Min duration (days): {min(durations)}, Max: {max(durations)}")
            all_keys = set(x[0] for x in all_intervals)
            def stats_within(days, label):
                within = [x for x in all_intervals if x[1] < days]
                keys = set(x[0] for x in within)
                prs = set(x[2] for x in within)
                print(f"Keys with adjacent intervals within {label}: {len(keys)} ({100*len(keys)/len(all_keys):.1f}%)")
            
            stats_within(30, "a month")
            stats_within(365, "a year")

            # Top 10 shortest adjacent modification intervals (verbose output removed)

    return key_timeline, multi_modified

def analyze_comments_in_prs(output_file: str):
    """
    Output CSV: pr_url, comments_cnt_dict (JSON dict of author -> comment count).
    Supports resuming from existing file.
    """
    import json
    from collections import defaultdict

    _, rows = parse_input_file()
    fieldnames = ["pr_url", "comments_cnt_dict"]

    # Load existing results for resuming
    existing = {}
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        with open(output_file, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                existing[row['pr_url']] = row
        # print(f"Loaded {len(existing)} existing results")  # Verbose output removed

    results = []
    for i, row in enumerate(rows, start=1):
        pr_url = row["pr_url"]
        
        if pr_url in existing:
            results.append(existing[pr_url])
            continue

        owner, repo, pr_number = parse_github_pr_url(pr_url)
        cnt = defaultdict(int)

        try:
            # 1) timeline: issue comment + review summary
            for ev in github_api_get(f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/timeline?per_page=100"):
                if ev.get("event") in ("commented", "reviewed") and (ev.get("body") or "").strip():
                    login = ((ev.get("user") or {}).get("login") or "").strip()
                    if login:
                        cnt[login] += 1

            # 2) inline review comments
            for c in github_api_get(f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments?per_page=100"):
                if (c.get("body") or "").strip():
                    login = ((c.get("user") or {}).get("login") or "").strip()
                    if login:
                        cnt[login] += 1
        except Exception as e:
            print(f"[{i}/{len(rows)}] Error {pr_url}: {e}")

        results.append({"pr_url": pr_url, "comments_cnt_dict": json.dumps(dict(cnt), ensure_ascii=False)})
        # print(f"[{i}/{len(rows)}] {pr_url}")  # Verbose output removed

        # Checkpoint save
        if i % 50 == 0:
            with open(output_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)

    # Final save
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    # print(f"Saved {len(results)} results to {output_file}")  # Verbose output removed
    
    _analyze_comments_results(output_file)


def _analyze_comments_results(output_file: str):
    """Analyze comment statistics."""
    is_bot = lambda x: 'bot' in x.lower()
    comments = load_data(output_file)
    commits = load_data(COMMITS_FILE)
    
    # (total_comments, has_manual, has_bot)
    data = [(sum(cnt.values()), any(not is_bot(u) for u in cnt), any(is_bot(u) for u in cnt)) for cnt in comments.values()]
    n, total = len(data), sum(d[0] for d in data)
    manual, bot = sum(d[1] for d in data), sum(d[2] for d in data)
    
    print(f"\n=== Comments Analysis ===")
    print(f"Total PRs: {n}, Total comments: {total}, Avg: {total/n:.2f}")
    print(f"PRs with manual comments: {manual} ({100*manual/n:.1f}%)")
    print(f"PRs with bot comments: {bot} ({100*bot/n:.1f}%)")
    
    # Analyze by commit status
    with_commits = [(sum(c.values()), any(not is_bot(u) for u in c)) for pr, c in comments.items() if commits.get(pr, 0) > 0]
    no_commits = [(sum(c.values()), any(not is_bot(u) for u in c)) for pr, c in comments.items() if commits.get(pr, 0) == 0]
    
    print(f"\n--- PRs with further commits ({len(with_commits)}) ---")
    print(f"Avg comments: {sum(d[0] for d in with_commits)/len(with_commits):.2f}, Manual review: {sum(d[1] for d in with_commits)} ({100*sum(d[1] for d in with_commits)/len(with_commits):.1f}%)")
    print(f"--- PRs without further commits ({len(no_commits)}) ---")
    print(f"Avg comments: {sum(d[0] for d in no_commits)/len(no_commits):.2f}, Manual review: {sum(d[1] for d in no_commits)} ({100*sum(d[1] for d in no_commits)/len(no_commits):.1f}%)")

def analyze_key_packages(input_file: str):
    """
    Analyze key packages consistency across platforms, following make_suggestion heuristics.
    """
    import re
    import sys
    from collections import defaultdict
    
    csv.field_size_limit(sys.maxsize)
    _PY_PAT = re.compile(r'^python(\d)-(.*)')  # python3-foo
    _PY_DIST_PAT = re.compile(r'^python(\d)dist\((.*)\)$')  # python3dist(foo)
    _CMAKE_PAT = re.compile(r'^cmake\((.*)\)$')  # cmake(foo)
    _PKGCONFIG_PAT = re.compile(r'^pkgconfig\((.*)\)$')  # pkgconfig(foo)
    
    def normalize(name: str) -> str:
        """
        Normalize package name strictly following make_suggestion heuristics:
        1. python[0-9]-foo -> foo
        2. lib prefix removal
        3. -dev / -devel equivalence
        4. cmake(foo) / pkgconfig(foo) -> foo
        5. python[0-9]dist(foo) -> foo
        6. - / _ equivalence
        """
        if not name: return ''
        name = name.lower().strip()
        
        # Rule 4: cmake(foo) -> foo
        m = _CMAKE_PAT.match(name)
        if m: name = m.group(1)
        # Rule 4: pkgconfig(foo) -> foo
        m = _PKGCONFIG_PAT.match(name)
        if m: name = m.group(1)
        # Rule 5: python3dist(foo) -> foo
        m = _PY_DIST_PAT.match(name)
        if m: name = m.group(2)
        # Rule 1: python(\d)-foo -> foo
        m = _PY_PAT.match(name)
        if m: name = m.group(2)
        # Rule 2: lib prefix removal
        if name.startswith('lib'): name = name[3:]
        # Rule 3: -dev / -devel equivalence (remove both)
        if name.endswith('-devel'): name = name[:-6]
        elif name.endswith('-dev'): name = name[:-4]
        # Rule 6: - / _ equivalence
        return name.replace('-', '_')
    
    def get_normalized_set(pkg_names):
        s = {normalize(p) for p in pkg_names}
        s.discard('')
        return s
    
    # Track PRs that introduce more inconsistency
    all_prs = set()
    raw_inconsistent_prs = set()   # PRs where raw unique count increased
    norm_inconsistent_prs = set()  # PRs where normalized unique count increased
    
    with open(input_file, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            pr_url = row['pr_url']
            before_json, after_json = row.get('key_packages_before', ''), row.get('key_packages_after', '')
            if after_json in ('ERROR', '', '{}'): continue
            
            after_data = _deserialize_key_packages(after_json)
            before_data = _deserialize_key_packages(before_json) if before_json and before_json != 'ERROR' else {}
            if not after_data: continue
            
            all_prs.add(pr_url)
            
            # Check if this PR introduced inconsistency for any key
            for key, pkg_dict in after_data.items():
                aft_pkgs = list(pkg_dict.keys())
                aft_raw_cnt = len(set(aft_pkgs))
                aft_norm_cnt = len(get_normalized_set(aft_pkgs))
                
                if key in before_data:
                    # Existing key: check if inconsistency increased
                    bef_pkgs = list(before_data[key].keys())
                    bef_raw_cnt = len(set(bef_pkgs))
                    bef_norm_cnt = len(get_normalized_set(bef_pkgs))
                    
                    if aft_raw_cnt > bef_raw_cnt:
                        raw_inconsistent_prs.add(pr_url)
                    if aft_norm_cnt > bef_norm_cnt:
                        norm_inconsistent_prs.add(pr_url)
                else:
                    # New key: check if it's already inconsistent (>1 unique names)
                    if aft_raw_cnt > 1:
                        # print(aft_pkgs)  # Verbose output removed
                        raw_inconsistent_prs.add(pr_url)
                    if aft_norm_cnt > 1:
                        # print(aft_pkgs)
                        norm_inconsistent_prs.add(pr_url)
    
    # Get rule-related PRs and re-edited PRs
    _, all_rows = parse_input_file()
    rule_related_prs = {r['pr_url'] for r in all_rows if (ct := r.get('chained_modifications_types', '')) and ct not in ('No modifications found', 'ERROR') and '.' in ct and 'lint' not in ct.lower()}
    total_chained_modifications_prs = {r['pr_url'] for r in all_rows if (ct := r.get('chained_modifications_types', '')) and ct not in ('No modifications found', 'ERROR')}
    
    key_to_prs = defaultdict(list)
    with open(MODIFIED_KEYS_FILE, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if (ks := row.get('modified_keys', '')) and ks != 'ERROR':
                for k in ks.split(', '):
                    if k.strip(): key_to_prs[k.strip()].append((row.get('closed_at', ''), row['pr_url']))
    
    re_edited_prs = set()
    for pr_list in key_to_prs.values():
        if len(pr_list) > 1:
            pr_list.sort()
            re_edited_prs.update(p for _, p in pr_list[1:])
    
    # Filter to PRs in our dataset
    rule_related_prs &= all_prs
    re_edited_prs &= all_prs
    
    print(f"\n=== Inconsistent PRs Analysis ===")
    print(f"Total PRs analyzed: {len(all_prs)}")
    
    # --- Raw (without normalization) ---
    raw_incons = raw_inconsistent_prs
    raw_consistent = all_prs - raw_incons
    print(f"\n--- Raw (without normalization) ---")
    print(f"Inconsistent PRs (raw): {len(raw_incons)} ({100*len(raw_incons)/len(all_prs):.1f}%)")
    print(f"  - Rule-related in all PRs:        {len(rule_related_prs)}/{len(all_prs)} = {100*len(rule_related_prs)/len(all_prs):.1f}%")
    print(f"  - Rule-related in inconsistent:   {len(rule_related_prs & raw_incons)}/{len(raw_incons)} = {100*len(rule_related_prs & raw_incons)/len(raw_incons):.1f}%" if raw_incons else "N/A")
    print(f"  - Rule-related in consistent:     {len(rule_related_prs & raw_consistent)}/{len(raw_consistent)} = {100*len(rule_related_prs & raw_consistent)/len(raw_consistent):.1f}%" if raw_consistent else "N/A")
    print(f"  - Chained modifications in consistent:     {len(total_chained_modifications_prs & raw_consistent)}/{len(raw_consistent)} = {100*len(total_chained_modifications_prs & raw_consistent)/len(raw_consistent):.1f}%" if raw_consistent else "N/A")
    print(f"  - Chained modifications in inconsistent:     {len(total_chained_modifications_prs & raw_incons)}/{len(raw_incons)} = {100*len(total_chained_modifications_prs & raw_incons)/len(raw_incons):.1f}%" if raw_incons else "N/A")
    
    
    print(f"  - Re-edited in all PRs:           {len(re_edited_prs)}/{len(all_prs)} = {100*len(re_edited_prs)/len(all_prs):.1f}%")
    print(f"  - Re-edited in inconsistent:      {len(re_edited_prs & raw_incons)}/{len(raw_incons)} = {100*len(re_edited_prs & raw_incons)/len(raw_incons):.1f}%" if raw_incons else "N/A")
    
    # --- Normalized ---
    norm_incons = norm_inconsistent_prs
    print(f"\n--- Normalized ---")
    print(f"Inconsistent PRs (normalized): {len(norm_incons)} ({100*len(norm_incons)/len(all_prs):.1f}%)")
    print(f"  - Rule-related in all PRs:        {len(rule_related_prs)}/{len(all_prs)} = {100*len(rule_related_prs)/len(all_prs):.1f}%")
    print(f"  - Rule-related in inconsistent:   {len(rule_related_prs & norm_incons)}/{len(norm_incons)} = {100*len(rule_related_prs & norm_incons)/len(norm_incons):.1f}%" if norm_incons else "N/A")
    print(f"  - Re-edited in all PRs:           {len(re_edited_prs)}/{len(all_prs)} = {100*len(re_edited_prs)/len(all_prs):.1f}%")
    print(f"  - Re-edited in inconsistent:      {len(re_edited_prs & norm_incons)}/{len(norm_incons)} = {100*len(re_edited_prs & norm_incons)/len(norm_incons):.1f}%" if norm_incons else "N/A")
    
    return {'all_prs': all_prs, 'raw_inconsistent_prs': raw_incons, 'norm_inconsistent_prs': norm_incons,
            'rule_related_prs': rule_related_prs, 're_edited_prs': re_edited_prs}


def analyze_chained_modification_issues(output_file: str):
    """ Analyze chained modification issues. """
    
    commits_data = load_data(COMMITS_FILE)
    committed_prs = {pr for pr, n in commits_data.items() if int(n) != 0}
    rule_related_modifications = set()
    linter_only_related_modifications = set()
    _, rows = parse_input_file()
    for row in rows:
        pr_url = row['pr_url']
        chained_modifications_types = row['chained_modifications_types']
        if chained_modifications_types == 'No modifications found' or chained_modifications_types == 'ERROR':
          continue
        if 'lint' in chained_modifications_types:
          linter_only_related_modifications.add(pr_url)
        elif '.' in chained_modifications_types:
          rule_related_modifications.add(pr_url)
    print("-------------PRs received modifications after initial review-------------------")
    print(f"Prs recived modifcations after initial review : {len(linter_only_related_modifications|rule_related_modifications)}")
    print(f"Percentage of re-committed PRs: {100*len(linter_only_related_modifications|rule_related_modifications)/len(rows):.1f}%")
    print(f"Rule unrelated modifications (like key): {len(linter_only_related_modifications)}")
    print(f"Rule related modifications: {len(rule_related_modifications)}")

def override_chained_modifications_from_file():
    """Override chained_modifications_types in pr_manual_analyzed_final.csv with values from pr_chained_modification.csv"""

    pr_chained_file = str(DATA_DIR / 'pr_chained_modification.csv')
    
    # Read pr_chained_modification.csv to get the mapping
    chained_mapping = {}
    with open(pr_chained_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            chained_mapping[row['pr_url']] = row['chained_modifications_types']
    
    print(f"Loaded {len(chained_mapping)} entries from {pr_chained_file}")
    
    # Read all rows from the input file
    all_rows = []
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        for row in reader:
            all_rows.append(dict(row))
    
    # Update chained_modifications_types if pr_url exists in pr_chained_modification.csv
    updated_count = 0
    for row in all_rows:
        pr_url = row['pr_url']
        if pr_url in chained_mapping:
            old_value = row['chained_modifications_types']
            new_value = chained_mapping[pr_url]
            if old_value != new_value:
                print(f"Updating {pr_url}: '{old_value}' -> '{new_value}'")
                row['chained_modifications_types'] = new_value
                updated_count += 1

 
   
def main():
    analyze_time_durations()
    collect_commits_after_review(COMMITS_FILE)
    collect_pr_modified_keys(MODIFIED_KEYS_FILE)
    collect_key_packages(KEY_PACKAGES_FILE)
    analyze_key_packages(KEY_PACKAGES_FILE)
    analyze_comments_in_prs(COMMENTS_FILE)
    analyze_chained_modification_issues(CHAINED_MODIFICATIONS_FILE)
    
    
if __name__ == '__main__':
    main()



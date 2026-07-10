#!/usr/bin/env python3

import os
import pprint
import re
import subprocess
import time
from io import StringIO
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import unidiff
import yaml

from rosdep_auditor.config import load_config
from rosdep_auditor.tools.yaml import AnnotatedSafeLoader
from rosdep_auditor.tools.yaml import isolate_yaml_snippets_from_line_numbers
from rosdep_auditor import get_package_link
from rosdep_auditor.tools import logger

from rosdep_auditor.distro_parser import RosdepDatabase

# Get token from environment variable for double-blind compliance
_github_token = os.environ.get('GITHUB_TOKEN', '')
headers = {
        'Accept': 'application/vnd.github+json',
}
if _github_token:
        headers['Authorization'] = f'token {_github_token}'


def _create_retry_session(retries=5, backoff_factor=1):
    """Create a requests session with retry logic for handling transient network errors."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        # Raise error on redirect so we don't silently follow redirects
        raise_on_redirect=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Create a global session with retry logic
_retry_session = _create_retry_session()


def _get_with_retry(url, session_headers, timeout=30):
    """Make a GET request with retry logic for SSL and other transient errors."""
    for attempt in range(6):  # 6 attempts total
        try:
            response = _retry_session.get(url, headers=session_headers, timeout=timeout)
            response.raise_for_status()
            return response
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.RequestException) as e:
            if attempt < 5:  # Don't sleep on the last attempt
                wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                logger.debug(f"Request failed (attempt {attempt + 1}/6): {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise  # Re-raise the exception on the final attempt


def get_pr_commits_list(owner: str, repo: str, pr_number: str) -> list:
    """Fetches the list of commits for a given pull request."""
    # API returns commits in chronological order (oldest first).
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/commits"
    # This endpoint returns JSON, so current global 'headers' 'Accept' type is fine.
    response = _get_with_retry(url, headers)
    time.sleep(2)  # Respect rate limits
    return response.json()


def get_diff_between_shas(owner: str, repo: str, base_sha: str, head_sha: str) -> str:
    """Fetches the diff text between two commit SHAs."""
    # https://github.com/ros/rosdistro/compare/398d360632f8b9520d7ffec7c3df82ed93a39e8d...e87c30e25bbcea6262d2c008bf08ef4963c64388.diff
    url = f"https://github.com/{owner}/{repo}/compare/{base_sha}...{head_sha}.diff"
    # Prepare headers for diff format
    diff_headers = headers.copy()  # Start with global headers (for Authorization)
    diff_headers['Accept'] = 'application/vnd.github.v3.diff'

    response = _get_with_retry(url, diff_headers)
    time.sleep(2)  # Respect rate limits
    return response.content.decode('utf-8')

def detect_lines(diffstr):
    """
    Parses a diff string to identify added lines, simulating the line number
    output as if a --unified=0 diff was processed by earlier logic.
    File paths are normalized for dictionary keys (e.g., 'rosdep/python.yaml').
    """
    added_lines = {}
    removed_lines = {}

    with StringIO(diffstr) as io_diffstr:
        patch_set = unidiff.PatchSet(io_diffstr)

    for patched_file in patch_set:
        # Use target_file for path; skip if it's a deleted file (target is /dev/null)
        path_for_key = patched_file.target_file
        if path_for_key == '/dev/null' and patched_file.is_removed_file:
            continue

        # Normalize path: remove 'a/' or 'b/' prefixes.
        # This makes keys like 'rosdep/python.yaml' for compatibility
        # with how your test script's setUpClass accesses these paths.
        normalized_file_key = path_for_key
        if normalized_file_key.startswith(('a/', 'b/')): # Checks for either prefix
            normalized_file_key = normalized_file_key[2:]

        current_file_added_lines = []
        current_file_removed_lines = []

        for hunk in patched_file:
            for line in hunk:
                if line.is_added and line.target_line_no is not None:
                    current_file_added_lines.append(line.target_line_no)
                elif line.is_removed and line.source_line_no is not None:
                    current_file_removed_lines.append(line.source_line_no)

        if current_file_added_lines:
            # Store sorted, unique line numbers for consistency.
            added_lines[normalized_file_key] = sorted(list(set(current_file_added_lines)))
        if current_file_removed_lines:
            removed_lines[normalized_file_key] = sorted(list(set(current_file_removed_lines)))

    return added_lines, removed_lines



def detect_edited_files(diffstr, keep_orginal_file=False):
    """
    Parses a diff string to identify edited files, simulating the line number
    output as if a --unified=0 diff was processed by earlier logic.
    File paths are normalized for dictionary keys (e.g., 'rosdep/python.yaml').
    """
    resultant_files = {}
    with StringIO(diffstr) as io_diffstr:
        patch_set = unidiff.PatchSet(io_diffstr)

    for patched_file in patch_set:
        if 'distribution.yaml' in patched_file.target_file and not keep_orginal_file:
          resultant_files['distribution.yaml'] = True
        else:
          path_for_key = patched_file.target_file
          if path_for_key.startswith(('a/', 'b/')): # Checks for either prefix
            path_for_key = path_for_key[2:]
          resultant_files[path_for_key] = True

    return resultant_files

def parse_github_pr_url(pr_url):
    """Parse GitHub PR URL to extract owner, repo, and PR number"""
    pattern = r'https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)'
    match = re.match(pattern, pr_url)

    if not match:
        raise ValueError(f"Invalid GitHub PR URL: {pr_url}")

    return match.groups()


def get_pr_details(owner, repo, pr_number ):
    """Get PR details from GitHub API"""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    response = _get_with_retry(url, headers)
    time.sleep(2)
    return response.json()


def get_pr_diff(url) -> str:
    """Get PR diff using GitHub's compare API"""
    response = _get_with_retry(url, headers)
    time.sleep(2)
    return response.content.decode("utf-8")


def get_file_content(owner, repo, ref, file_path):
    """Get file content from GitHub API"""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}?ref={ref}"
    try:
        response = _get_with_retry(url, headers)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code != 200:
            return None
        raise
    time.sleep(2)
    import base64
    content = base64.b64decode(response.json()["content"]).decode("utf-8")
    return content


def _classify_rosdep_changes_in_file(
    file_content_before_str: str | None,
    file_content_after_str: str | None,
    path_key: str,  # e.g., 'rosdep/base.yaml'
    affected_keys_from_diff: set[str], # Changed from added_lines & removed_lines
    diff_files: dict  # Result from detect_edited_files()
) -> set[str]:
    """Classifies changes within a single rosdep file based on its before and after content and affected keys."""
    types_found = set()

    full_data_before_at_path = {}
    if file_content_before_str:
        try:
            loaded_data = yaml.load(file_content_before_str, Loader=AnnotatedSafeLoader)
            if isinstance(loaded_data, dict):
                full_data_before_at_path = loaded_data
            else:
                # If loaded_data is not a dict (e.g. empty file, or just a list/string), treat as empty dict for key analysis
                logger.warning(f"Expected dict from YAML, got {type(loaded_data)} for {path_key} (before). Treating as empty.")
        except yaml.YAMLError as e:
            logger.warning(f"Error parsing YAML for {path_key} (before): {e}. Treating as empty.")

    full_data_after_at_path = {}
    if file_content_after_str:
        try:
            loaded_data = yaml.load(file_content_after_str, Loader=AnnotatedSafeLoader)
            if isinstance(loaded_data, dict):
                full_data_after_at_path = loaded_data
            else:
                logger.warning(f"Expected dict from YAML, got {type(loaded_data)} for {path_key} (after). Treating as empty.")
        except yaml.YAMLError as e:
            logger.warning(f"Error parsing YAML for {path_key} (after): {e}. Treating as empty.")

    rosdistro_database_before = RosdepDatabase()
    if file_content_before_str: # RosdepDatabase.load_yaml can handle empty/invalid YAML string gracefully
        rosdistro_database_before.load_yaml(file_content_before_str)

    rosdistro_database_after = RosdepDatabase()
    if file_content_after_str:
        rosdistro_database_after.load_yaml(file_content_after_str)

    total_keys_before = set(full_data_before_at_path.keys())
    total_keys_after = set(full_data_after_at_path.keys())

    keys_to_analyze = affected_keys_from_diff
    if not affected_keys_from_diff:
        # If no specific keys were isolated from the diff lines (e.g., new file, deleted file, or changes not caught by isolator),
        # then consider all keys that exist in either the before or after state for determining adds/removals/edits.
        logger.debug(f"No specific keys isolated from diff for {path_key}. Analyzing all key differences between states.")
        keys_to_analyze = total_keys_before | total_keys_after
    else:
        logger.debug(f"Analyzing specific keys isolated from diff for {path_key}: {affected_keys_from_diff}")

    # Determine added, removed, and edited keys based on the scope defined by keys_to_analyze.
    added_keys = (total_keys_after - total_keys_before) & keys_to_analyze
    removed_keys = (total_keys_before - total_keys_after) & keys_to_analyze
    edited_keys = (total_keys_before & total_keys_after) & keys_to_analyze

    for key in added_keys:
        if key + '-pip' in removed_keys or key + '-pip' in edited_keys: # Check against original removed/edited sets
            logger.info(f"Type B.2a: make the pip installed package install by system package manager: {key}")
            types_found.add("B.2a")
        else:
            logger.info(f"Type A.1: Add a new rosdep key: {key}")
            types_found.add("A.1")

    for key in removed_keys:
        # Check if it was a -pip key and its non-pip counterpart wasn't added, and distribution.yaml changed
        if key.endswith('-pip') and \
           key.replace('-pip', '') not in added_keys and \
           'distribution.yaml' in diff_files: # diff_files keys are typically like 'rosdep/foo.yaml' or 'distribution.yaml'
            logger.info(f"Type B.2a: make the pip installed package to be installed from ROS repository: {key}")
            types_found.add("B.2a")

        if key.endswith('-pip') and \
           key.endswith('-pip') in total_keys_before:
            logger.info(f"Type B.2a: make the pip installed package install by system package manager: {key}")
            types_found.add("B.2a")

    for key in edited_keys:
        previous_entry = rosdistro_database_before.get_entry(key)
        current_entry = rosdistro_database_after.get_entry(key)

        if not previous_entry or not current_entry: # Should not happen for edited_keys if data loaded
            continue

        # Check for Type A.2: Add new platform or OS versions
        found_type_A_2 = False
        for platform in current_entry.get_platforms():
            if platform not in previous_entry.get_platforms() and '*' not in previous_entry.get_platforms():
                logger.info(f"Type A.2: Add new platform to an existing rosdep key: {key} ({platform})")
                types_found.add("A.2")
                found_type_A_2 = True
                break
            for version in current_entry.get_versions(platform):
                if version not in previous_entry.get_versions(platform) and '*' not in previous_entry.get_versions(platform) and '*' not in previous_entry.get_platforms():
                    logger.info(f"Type A.2: Add new OS version to an existing rosdep key: {key} ({platform}:{version})")
                    types_found.add("A.2")
                    found_type_A_2 = True
                    break
            if found_type_A_2: break

        # if found_type_A_2:
        #     continue # Allow further checks for the same key

        # Check for Type B.2a: pip to system package manager update
        found_type_B_2a_update = False
        for platform in current_entry.get_platforms():
            for version in current_entry.get_versions(platform):
                    current_platform = platform
                    current_version = version
                    previous_platform, previous_version = previous_entry.get_platform_and_version(platform, version)
                    if previous_platform is not None and previous_version is not None:
                        previous_package_manager = previous_entry.get_package_manager(previous_platform, previous_version)
                        current_package_manager = current_entry.get_package_manager(current_platform, current_version)
                        if previous_package_manager == 'pip' and current_package_manager != 'pip':
                            logger.info(f"Type B.2a: Update the pip installed package to be installed by system package manager: {key}")
                            types_found.add("B.2a")
                            found_type_B_2a_update = True
                            break
            if found_type_B_2a_update: break

        # if found_type_B_2a_update:
        #     continue # Allow further checks for the same key

        # Check for Type B.1a: Update the value of a rosdep key (generic)
        # This will now be checked regardless of whether A.2 or B.2a were found for the same key.
        found_type_B_1a = False
        for platform in current_entry.get_platforms():
            for version in current_entry.get_versions(platform):
                current_platform = platform
                current_version = version
                previous_platform, previous_version = previous_entry.get_platform_and_version(platform, version)
                if previous_platform is not None and previous_version is not None:
                  current_package_data = current_entry.get_package_details(current_platform, current_version)
                  previous_package_data = previous_entry.get_package_details(previous_platform, previous_version)
                  if current_package_data != previous_package_data:
                        logger.info(f"Type B.1a: Update the value of a rosdep key: {key} ({platform}:{version})")
                        types_found.add("B.1a")
                        found_type_B_1a = True
                        break
            if found_type_B_1a: break

        # check for type B.1a that current entry is less than previous entry
        for platform in previous_entry.get_platforms():
            for version in previous_entry.get_versions(platform):
                previous_platform = platform
                previous_version = version
                current_platform, current_version = current_entry.get_platform_and_version(platform, version)
                if current_platform is not None and current_version is not None:
                    current_package_data = current_entry.get_package_details(current_platform, current_version)
                    previous_package_data = previous_entry.get_package_details(previous_platform, previous_version)
                    if current_package_data != previous_package_data:
                        logger.info(f"Type B.1a: Update the value of a rosdep key: {key} ({platform}:{version})")
                        types_found.add("B.1a")
                        found_type_B_1a = True
                        break

    return types_found

def analyze_pr_classification(pr_url) -> set[str]:
    """Analyze a PR from its GitHub URL using GitHub's API"""
    owner, repo, pr_number = parse_github_pr_url(pr_url)
    pr_details = get_pr_details(owner, repo, pr_number)

    head_sha = pr_details["head"]["sha"]
    base_sha = pr_details["base"]["sha"]

    raw_diff = get_pr_diff(pr_details["diff_url"])
    diff_files = detect_edited_files(raw_diff)
    added_lines, removed_lines = detect_lines(raw_diff)
    if not added_lines and not removed_lines and not diff_files:
        # If no lines changed AND no files were detected as edited (e.g. new/deleted empty files)
        return set()

    all_types_found = set()

    for path in ('rosdep/base.yaml', 'rosdep/python.yaml'):
        file_content_before_str = get_file_content(owner, repo, base_sha, path)
        file_content_after_str = get_file_content(owner, repo, head_sha, path)

        parsed_yaml_before = {}
        if file_content_before_str:
            try:
                loaded_data = yaml.load(file_content_before_str, Loader=AnnotatedSafeLoader)
                if isinstance(loaded_data, dict): parsed_yaml_before = loaded_data
            except yaml.YAMLError: pass # Error logged in _classify if it matters

        parsed_yaml_after = {}
        if file_content_after_str:
            try:
                loaded_data = yaml.load(file_content_after_str, Loader=AnnotatedSafeLoader)
                if isinstance(loaded_data, dict): parsed_yaml_after = loaded_data
            except yaml.YAMLError: pass

        path_added_lines = added_lines.get(path, [])
        path_removed_lines = removed_lines.get(path, [])

        isolated_snippets_after = {}
        if path_added_lines and parsed_yaml_after:
            isolated_snippets_after = isolate_yaml_snippets_from_line_numbers(
                parsed_yaml_after, path_added_lines)


        isolated_snippets_before = {}
        if path_removed_lines and parsed_yaml_before:
            isolated_snippets_before = isolate_yaml_snippets_from_line_numbers(
                parsed_yaml_before, path_removed_lines)

        affected_keys_for_path = set(isolated_snippets_after.keys()) | set(isolated_snippets_before.keys())

        # If no specific keys were isolated from line changes, but the file is in diff_files,
        # it might be a complete creation or deletion, or a change not isolatable by current logic.
        # _classify_rosdep_changes_in_file will compare full key sets if affected_keys_for_path is empty
        # and rely on its intersection logic. So, we can proceed.
        # However, if the file wasn't in diff_files AND no keys were isolated, then skip.
        if not affected_keys_for_path and path not in diff_files:
            logger.debug(f"No changes detected for {path} (neither line-isolated keys nor in overall diff_files list), skipping.")
            continue

        types_for_path = _classify_rosdep_changes_in_file(
            file_content_before_str,
            file_content_after_str,
            path,
            affected_keys_for_path,
            diff_files
        )
        all_types_found.update(types_for_path)

    if not all_types_found:
        logger.info("No specific rosdep change types classified based on the PR diff.")

    return all_types_found

def analyze_pr_classification_with_keys(pr_url) -> tuple[set[str], set[str]]:
    """Analyze a PR and return both classification types and modified keys."""
    owner, repo, pr_number = parse_github_pr_url(pr_url)
    pr_details = get_pr_details(owner, repo, pr_number)

    head_sha = pr_details["head"]["sha"]
    base_sha = pr_details["base"]["sha"]

    raw_diff = get_pr_diff(pr_details["diff_url"])
    diff_files = detect_edited_files(raw_diff)
    added_lines, removed_lines = detect_lines(raw_diff)
    if not added_lines and not removed_lines and not diff_files:
        return set(), set()

    all_types_found = set()
    all_modified_keys = set()

    for path in ('rosdep/base.yaml', 'rosdep/python.yaml'):
        file_content_before_str = get_file_content(owner, repo, base_sha, path)
        file_content_after_str = get_file_content(owner, repo, head_sha, path)

        parsed_yaml_before = {}
        if file_content_before_str:
            try:
                loaded_data = yaml.load(file_content_before_str, Loader=AnnotatedSafeLoader)
                if isinstance(loaded_data, dict): parsed_yaml_before = loaded_data
            except yaml.YAMLError: pass

        parsed_yaml_after = {}
        if file_content_after_str:
            try:
                loaded_data = yaml.load(file_content_after_str, Loader=AnnotatedSafeLoader)
                if isinstance(loaded_data, dict): parsed_yaml_after = loaded_data
            except yaml.YAMLError: pass

        path_added_lines = added_lines.get(path, [])
        path_removed_lines = removed_lines.get(path, [])

        isolated_snippets_after = {}
        if path_added_lines and parsed_yaml_after:
            isolated_snippets_after = isolate_yaml_snippets_from_line_numbers(
                parsed_yaml_after, path_added_lines)

        isolated_snippets_before = {}
        if path_removed_lines and parsed_yaml_before:
            isolated_snippets_before = isolate_yaml_snippets_from_line_numbers(
                parsed_yaml_before, path_removed_lines)

        affected_keys_for_path = set(isolated_snippets_after.keys()) | set(isolated_snippets_before.keys())

        if not affected_keys_for_path and path not in diff_files:
            logger.debug(f"No changes detected for {path} (neither line-isolated keys nor in overall diff_files list), skipping.")
            continue

        # Collect modified keys (without file prefix)
        for key in affected_keys_for_path:
            all_modified_keys.add(key)

        types_for_path = _classify_rosdep_changes_in_file(
            file_content_before_str,
            file_content_after_str,
            path,
            affected_keys_for_path,
            diff_files
        )
        all_types_found.update(types_for_path)

    if not all_types_found:
        logger.info("No specific rosdep change types classified based on the PR diff.")

    return all_types_found, all_modified_keys


def analyze_pr_modifications(pr_url) -> set[str]:
    """Analyze a PR from its GitHub URL using GitHub's API by comparing the first and last PR commits."""
    owner, repo, pr_number = parse_github_pr_url(pr_url)

    pr_commits = get_pr_commits_list(owner, repo, pr_number)
    if not pr_commits:
        logger.debug(f"No commits found for PR: {pr_url}. Cannot analyze modifications within PR.")
        return set()

    first_pr_commit_sha = pr_commits[0]['sha']
    pr_details = get_pr_details(owner, repo, pr_number)
    for commit in reversed(pr_commits):
        if not 'merge' in commit['commit']['message'].lower():
            last_pr_commit_sha = commit['sha']
            break

    if first_pr_commit_sha == last_pr_commit_sha:
        return set()

    intra_pr_diff_str = get_diff_between_shas(owner, repo, first_pr_commit_sha, last_pr_commit_sha)

    added_lines_intra_pr, removed_lines_intra_pr = detect_lines(intra_pr_diff_str)
    diff_files_intra_pr = detect_edited_files(intra_pr_diff_str)

    if not added_lines_intra_pr and not removed_lines_intra_pr and not diff_files_intra_pr:
        return set()

    all_types_found_intra_pr = set()

    for path in ('rosdep/base.yaml', 'rosdep/python.yaml'):
        file_content_at_first_commit_str = get_file_content(owner, repo, first_pr_commit_sha, path)
        file_content_at_last_commit_str = get_file_content(owner, repo, last_pr_commit_sha, path)

        parsed_yaml_at_first_commit = {}
        if file_content_at_first_commit_str:
            try:
                loaded_data = yaml.load(file_content_at_first_commit_str, Loader=AnnotatedSafeLoader)
                if isinstance(loaded_data, dict): parsed_yaml_at_first_commit = loaded_data
            except yaml.YAMLError: pass

        parsed_yaml_at_last_commit = {}
        if file_content_at_last_commit_str:
            try:
                loaded_data = yaml.load(file_content_at_last_commit_str, Loader=AnnotatedSafeLoader)
                if isinstance(loaded_data, dict): parsed_yaml_at_last_commit = loaded_data
            except yaml.YAMLError: pass

        path_added_lines = added_lines_intra_pr.get(path, [])
        path_removed_lines = removed_lines_intra_pr.get(path, [])

        isolated_snippets_after_intra_pr = {}
        if path_added_lines and parsed_yaml_at_last_commit: # 'after' state is last commit
            isolated_snippets_after_intra_pr = isolate_yaml_snippets_from_line_numbers(
                parsed_yaml_at_last_commit, path_added_lines)

        isolated_snippets_before_intra_pr = {}
        if path_removed_lines and parsed_yaml_at_first_commit: # 'before' state is first commit
            isolated_snippets_before_intra_pr = isolate_yaml_snippets_from_line_numbers(
                parsed_yaml_at_first_commit, path_removed_lines)

        affected_keys_for_path_intra_pr = set(isolated_snippets_after_intra_pr.keys()) | set(isolated_snippets_before_intra_pr.keys())

        if not affected_keys_for_path_intra_pr and path not in diff_files_intra_pr:
            logger.debug(f"No modifications for {path} (neither line-isolated keys nor in intra-PR diff_files), skipping classification.")
            continue

        types_for_path = _classify_rosdep_changes_in_file(
            file_content_at_first_commit_str,
            file_content_at_last_commit_str,
            path,
            affected_keys_for_path_intra_pr,
            diff_files_intra_pr
        )
        all_types_found_intra_pr.update(types_for_path)

    if not all_types_found_intra_pr:
        logger.debug(f"No specific rosdep change types classified from modifications within PR {pr_url}.")

    return all_types_found_intra_pr


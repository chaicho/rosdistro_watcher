#!/usr/bin/env python3
"""Build the frozen RQ6 prospective-validation PR database skeleton.

This script fetches merged rosdep PRs via the GitHub Search API, extracts
modified rosdep keys from each PR diff (without classification), and filters
to those whose modified keys all exist in the frozen rosdep snapshot.
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(TOOL_PATH))

DATA_DIR = TOOL_PATH / "artifact" / "RQ6" / "data"
PROSPECTIVE_DATABASE_FILE = DATA_DIR / "rq6_prospective_database.csv"
MAPPING_TEMPLATE_FILE = DATA_DIR / "pr_defect_types_mapping.csv"

DEFAULT_START_DATE = "2025-09-01"
DEFAULT_END_DATE = "2026-01-15"
DEFAULT_REPO_OWNER = "ros"
DEFAULT_REPO_NAME = "rosdistro"
DEFAULT_LABEL = "rosdep"

# PRs authored by the RosdepAuditor team — excluded from the prospective set
# to avoid evaluating the detector on our own contributions.
IGNORE_PR_URLS = {
    "https://github.com/ros/rosdistro/pull/47662",
    "https://github.com/ros/rosdistro/pull/48183",
}

DATABASE_COLUMNS = [
    "pr_url",
    "pr_number",
    "pr_title",
    "created_at",
    "closed_at",
    "modified_keys",
]

MAPPING_COLUMNS = [
    "pr",
    "keys",
    "defect_types_addressed_in_pr",
    "defect_types_detect_from_keys",
    "use_our_packages",
    "description",
]


def load_github_token() -> str:
    """Load the GitHub token from this repo's .env.github format."""
    env_path = TOOL_PATH / ".env.github"
    if not env_path.exists():
        print(f"Warning: {env_path} not found; using unauthenticated GitHub API requests.")
        return ""

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export GITHUB_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")

    print(f"Warning: export GITHUB_TOKEN=... not found in {env_path}; using unauthenticated requests.")
    return ""


GITHUB_TOKEN = load_github_token()
if GITHUB_TOKEN:
    os.environ.setdefault("GITHUB_TOKEN", GITHUB_TOKEN)

from rosdep_auditor.pr_analyzer import (  # noqa: E402
    detect_edited_files,
    detect_lines,
    get_file_content,
    get_pr_diff,
    get_pr_details,
    headers,
    parse_github_pr_url,
)
from rosdep_auditor.tools.yaml import AnnotatedSafeLoader, isolate_yaml_snippets_from_line_numbers  # noqa: E402
from generate_pr_defect_report import initialize_detection_system  # noqa: E402

if GITHUB_TOKEN:
    headers["Authorization"] = f"token {GITHUB_TOKEN}"


def github_search_headers() -> dict:
    request_headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        request_headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return request_headers


def normalize_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def fetch_merged_rosdep_prs(
    start_date: str,
    end_date: str,
    repo_owner: str = DEFAULT_REPO_OWNER,
    repo_name: str = DEFAULT_REPO_NAME,
    label: str = DEFAULT_LABEL,
    max_pages: int = 50,
    delay: float = 0.5,
) -> list[dict]:
    """Fetch merged rosdep PRs in [start_date, end_date)."""
    start_dt = normalize_timestamp(start_date)
    end_dt = normalize_timestamp(end_date)
    result: list[dict] = []
    seen_urls: set[str] = set()

    print(f"Fetching merged PRs from {repo_owner}/{repo_name}")
    print(f"  label={label}, created_at >= {start_date}, created_at < {end_date}")

    for page in range(1, max_pages + 1):
        query = (
            f"repo:{repo_owner}/{repo_name} is:pr is:merged label:{label} "
            f"created:{start_dt.strftime('%Y-%m-%d')}..{end_dt.strftime('%Y-%m-%d')}"
        )
        response = requests.get(
            "https://api.github.com/search/issues",
            headers=github_search_headers(),
            params={"q": query, "sort": "created", "order": "asc", "per_page": 100, "page": page},
            timeout=30,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            break

        for item in items:
            created_at = normalize_timestamp(item["created_at"])
            if created_at < start_dt or created_at >= end_dt:
                continue

            pr_url = item["html_url"]
            if pr_url in seen_urls:
                continue

            pr_number = str(item["number"])
            details = get_pr_details(repo_owner, repo_name, pr_number)
            result.append({
                "pr_url": pr_url,
                "pr_number": pr_number,
                "pr_title": item["title"],
                "created_at": item["created_at"],
                "closed_at": details.get("merged_at") or details.get("closed_at") or item.get("closed_at", ""),
            })
            seen_urls.add(pr_url)

        if len(items) < 100:
            break
        time.sleep(delay)

    print(f"Fetched {len(result)} merged rosdep PRs in range.")
    return result


def analyze_modified_keys(pr_url: str, delay: float = 0.5) -> str:
    """Return comma-separated modified rosdep keys for a PR without classifying defect types."""
    try:
        owner, repo, pr_number = parse_github_pr_url(pr_url)
        pr_details = get_pr_details(owner, repo, pr_number)
        raw_diff = get_pr_diff(pr_details["diff_url"])
        diff_files = detect_edited_files(raw_diff)
        added_lines, removed_lines = detect_lines(raw_diff)
        if not added_lines and not removed_lines and not diff_files:
            return ""

        modified_keys = set()
        for path in ("rosdep/base.yaml", "rosdep/python.yaml"):
            file_content_before = get_file_content(owner, repo, pr_details["base"]["sha"], path)
            file_content_after = get_file_content(owner, repo, pr_details["head"]["sha"], path)

            parsed_before = {}
            if file_content_before:
                try:
                    loaded = yaml.load(file_content_before, Loader=AnnotatedSafeLoader)
                    if isinstance(loaded, dict):
                        parsed_before = loaded
                except yaml.YAMLError:
                    pass

            parsed_after = {}
            if file_content_after:
                try:
                    loaded = yaml.load(file_content_after, Loader=AnnotatedSafeLoader)
                    if isinstance(loaded, dict):
                        parsed_after = loaded
                except yaml.YAMLError:
                    pass

            affected = set()
            if path in added_lines and parsed_after:
                affected.update(isolate_yaml_snippets_from_line_numbers(parsed_after, added_lines[path]).keys())
            if path in removed_lines and parsed_before:
                affected.update(isolate_yaml_snippets_from_line_numbers(parsed_before, removed_lines[path]).keys())
            modified_keys.update(affected)

        return ", ".join(sorted(modified_keys))
    except Exception as exc:
        print(f"Warning: failed to analyze modified keys for {pr_url}: {exc}")
        return ""
    finally:
        if delay > 0:
            time.sleep(delay)


def load_rosdep_key_set() -> set[str]:
    """Load keys from the frozen RQ6 rosdep snapshot."""
    _, _, rosdep_keys, _ = initialize_detection_system(
        str(TOOL_PATH / "rosdep_auditor" / "configs" / "config_provision.yaml")
    )
    return set(rosdep_keys)


def all_keys_exist(modified_keys: str, rosdep_keys: set[str]) -> bool:
    keys = [key.strip() for key in str(modified_keys).split(",") if key.strip()]
    return bool(keys) and all(key in rosdep_keys for key in keys)


def _extract_pr_number(pr_url: str) -> str:
    """Extract PR number from a GitHub PR URL like .../pull/12345."""
    return pr_url.rstrip("/").rsplit("/", 1)[-1]


def load_pr_rows_from_csv(csv_path: Path) -> list[dict]:
    """Load PR rows from a pre-built CSV (skipping slow per-PR GitHub analysis).

    The CSV must have at minimum ``pr_url`` and ``modified_keys`` columns.
    Missing columns are filled from best-effort fallbacks:
      - ``pr_number`` ← extracted from ``pr_url``
      - ``pr_title``  ← ``title`` column, if present
      - ``created_at`` / ``closed_at`` ← left empty if absent
    """
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])

        missing = {"pr_url", "modified_keys"} - fieldnames
        if missing:
            raise ValueError(
                f"Input CSV {csv_path} is missing required columns: {sorted(missing)}"
            )

        has_title = "pr_title" in fieldnames or "title" in fieldnames
        if not has_title:
            print(f"Note: no pr_title/title column in {csv_path}; titles will be empty.")

        rows = []
        for row in reader:
            pr_url = row["pr_url"]
            rows.append({
                "pr_url": pr_url,
                "pr_number": row.get("pr_number") or _extract_pr_number(pr_url),
                "pr_title": row.get("pr_title") or row.get("title", ""),
                "created_at": row.get("created_at", ""),
                "closed_at": row.get("closed_at", ""),
                "modified_keys": row["modified_keys"],
            })

    print(f"Loaded {len(rows)} PR rows from {csv_path} (skipping per-PR GitHub analysis).")
    return rows


def build_rq6_prospective_database(
    output_file: Path = PROSPECTIVE_DATABASE_FILE,
    mapping_file: Path = MAPPING_TEMPLATE_FILE,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    delay: float = 0.5,
    max_pages: int = 50,
    from_csv: str | None = None,
) -> bool:
    """Build the RQ6 prospective database and blank mapping template.

    If ``from_csv`` is provided, PR rows (including pre-computed modified_keys)
    are loaded directly from that CSV, bypassing all GitHub API calls.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if from_csv:
        analyzed_rows = load_pr_rows_from_csv(Path(from_csv))
    else:
        rows = fetch_merged_rosdep_prs(start_date, end_date, max_pages=max_pages, delay=delay)
        sorted_rows = sorted(rows, key=lambda row: normalize_timestamp(row["created_at"]))

        # Extract modified keys (lightweight — no defect-type classification)
        analyzed_rows = []
        for index, row in enumerate(sorted_rows, 1):
            print(f"[{index}/{len(sorted_rows)}] Analyzing modified keys for PR #{row['pr_number']}")
            analyzed_rows.append({
                "pr_url": row["pr_url"],
                "pr_number": row["pr_number"],
                "pr_title": row["pr_title"],
                "created_at": row["created_at"],
                "closed_at": row["closed_at"],
                "modified_keys": analyze_modified_keys(row["pr_url"], delay=delay),
            })

    # Filter: only keep PRs whose modified keys all exist in the rosdep snapshot
    rosdep_keys = load_rosdep_key_set()
    filtered_rows = [row for row in analyzed_rows if all_keys_exist(row["modified_keys"], rosdep_keys)]
    print(f"  after key-existence filter: {len(filtered_rows)} PRs")

    # Exclude RosdepAuditor team PRs to avoid evaluating on own contributions
    filtered_rows = [row for row in filtered_rows if row["pr_url"] not in IGNORE_PR_URLS]
    print(f"  after excluding team-authored PRs: {len(filtered_rows)} PRs")

    # Write prospective database
    with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATABASE_COLUMNS)
        writer.writeheader()
        writer.writerows(filtered_rows)

    # Write blank mapping template
    with Path(mapping_file).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MAPPING_COLUMNS)
        writer.writeheader()
        for row in filtered_rows:
            writer.writerow({
                "pr": row["pr_url"],
                "keys": row["modified_keys"],
                "defect_types_addressed_in_pr": "",
                "defect_types_detect_from_keys": "",
                "use_our_packages": "",
                "description": "",
            })

    print(f"Wrote {len(filtered_rows)} RQ6 prospective rows to {output_file}")
    print(f"Wrote blank PR-defect mapping template to {mapping_file}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the RQ6 prospective-validation PR database skeleton.")
    parser.add_argument("-o", "--output", default=str(PROSPECTIVE_DATABASE_FILE), help="Output prospective CSV path.")
    parser.add_argument("--mapping-output", default=str(MAPPING_TEMPLATE_FILE), help="Output blank mapping CSV path.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="Inclusive start date.")
    parser.add_argument("--end-date", default=DEFAULT_END_DATE, help="Exclusive end date.")
    parser.add_argument("--max-pages", type=int, default=2, help="Maximum GitHub Search result pages.")
    parser.add_argument("-d", "--delay", type=float, default=0, help="Delay between GitHub-backed calls.")
    parser.add_argument(
        "--from-csv",
        default=None,
        help="Load PR rows (with pre-computed modified_keys) from a CSV, bypassing all GitHub API calls.",
    )
    args = parser.parse_args()

    build_rq6_prospective_database(
        output_file=Path(args.output),
        mapping_file=Path(args.mapping_output),
        start_date=args.start_date,
        end_date=args.end_date,
        delay=args.delay,
        max_pages=args.max_pages,
        from_csv=args.from_csv,
    )


if __name__ == "__main__":
    main()

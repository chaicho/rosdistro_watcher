#!/usr/bin/env python3
"""Build the frozen RQ5 PR database skeleton.

The output is the PR-level dataset that should be manually annotated for RQ5.
It intentionally avoids any rolling intermediate PR list.
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

DATA_DIR = TOOL_PATH / "artifact" / "RQ5" / "data"
OUTPUT_FILE = DATA_DIR / "rq5_database.csv"
RQ1_DATABASE_FILE = TOOL_PATH / "artifact" / "RQ1" / "data" / "pr_manual_analyzed_final.csv"

DEFAULT_START_DATE = "2025-05-01"
DEFAULT_END_DATE = "2025-09-30"
DEFAULT_REPO_OWNER = "ros"
DEFAULT_REPO_NAME = "rosdistro"
DEFAULT_LABEL = "rosdep"

OUTPUT_COLUMNS = [
    "pr_url",
    "pr_number",
    "pr_title",
    "created_at",
    "closed_at",
    "modified_keys",
    "classification_types",
]


def load_github_token() -> str:
    """Load a GitHub token from the environment or repo-local .env.github."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("TOKEN")
    if token:
        return token.strip()

    env_file = TOOL_PATH / ".env.github"
    if not env_file.exists():
        print("Warning: .env.github not found; using unauthenticated GitHub requests.")
        return ""

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return line.strip().strip('"').strip("'")
        key, value = line.split("=", 1)
        if key.strip() in {"GITHUB_TOKEN", "GH_TOKEN", "TOKEN"}:
            return value.strip().strip('"').strip("'")

    print("Warning: no GitHub token found in .env.github; using unauthenticated GitHub requests.")
    return ""


GITHUB_TOKEN = load_github_token()
if GITHUB_TOKEN:
    os.environ["GITHUB_TOKEN"] = GITHUB_TOKEN

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


def pr_number_from_url(pr_url: str) -> str:
    return str(pr_url).rstrip("/").split("/")[-1]


def fetch_merged_rosdep_prs(
    start_date: str,
    end_date: str,
    repo_owner: str = DEFAULT_REPO_OWNER,
    repo_name: str = DEFAULT_REPO_NAME,
    label: str = DEFAULT_LABEL,
    max_pages: int = 20,
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
        url = "https://api.github.com/search/issues"
        response = requests.get(
            url,
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
                "source": "time_window",
            })
            seen_urls.add(pr_url)

        if len(items) < 100:
            break
        time.sleep(delay)

    print(f"Fetched {len(result)} merged rosdep PRs in range.")
    return result


def load_historical_b2_prs() -> list[dict]:
    """Load historical B.2 PRs from the RQ1 manually classified database."""
    if not RQ1_DATABASE_FILE.exists():
        raise FileNotFoundError(f"Required RQ1 database not found: {RQ1_DATABASE_FILE}")

    df = pd.read_csv(RQ1_DATABASE_FILE)
    b2_df = df[
        (df["status"] == "merged")
        & (df["classification_types"].astype(str).str.contains(r"B\.2", na=False))
    ].copy()

    rows: list[dict] = []
    for _, row in b2_df.iterrows():
        pr_url = row["pr_url"]
        rows.append({
            "pr_url": pr_url,
            "pr_number": pr_number_from_url(pr_url),
            "pr_title": row.get("title", ""),
            "created_at": row.get("created_at", ""),
            "closed_at": row.get("closed_at", ""),
            "source": "historical_b2",
        })

    print(f"Loaded {len(rows)} historical B.2 PRs from RQ1.")
    return rows


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


def build_rq5_database(
    output_file: Path = OUTPUT_FILE,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    delay: float = 1.0,
    max_pages: int = 20,
) -> bool:
    """Build the RQ5 database skeleton."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rows = fetch_merged_rosdep_prs(start_date, end_date, max_pages=max_pages, delay=delay)
    rows.extend(load_historical_b2_prs())

    deduped = {row["pr_url"]: row for row in rows}
    sorted_rows = sorted(
        deduped.values(),
        key=lambda row: normalize_timestamp(row["created_at"]),
    )

    output_rows = []
    for index, row in enumerate(sorted_rows, 1):
        print(f"[{index}/{len(sorted_rows)}] Analyzing modified keys for PR #{row['pr_number']}")
        modified_keys = analyze_modified_keys(row["pr_url"], delay=delay)
        output_rows.append({
            "pr_url": row["pr_url"],
            "pr_number": row["pr_number"],
            "pr_title": row["pr_title"],
            "created_at": row["created_at"],
            "closed_at": row["closed_at"],
            "modified_keys": modified_keys,
            "classification_types": "",
        })

    with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Wrote {len(output_rows)} RQ5 PR skeleton rows to {output_file}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the frozen RQ5 PR database skeleton.")
    parser.add_argument("-o", "--output", default=str(OUTPUT_FILE), help="Output CSV path.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="Inclusive start date.")
    parser.add_argument("--end-date", default=DEFAULT_END_DATE, help="Exclusive end date.")
    parser.add_argument("--max-pages", type=int, default=2, help="Maximum GitHub Search result pages.")
    parser.add_argument("-d", "--delay", type=float, default=0, help="Delay between GitHub-backed calls.")
    args = parser.parse_args()

    build_rq5_database(
        output_file=Path(args.output),
        start_date=args.start_date,
        end_date=args.end_date,
        delay=args.delay,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()

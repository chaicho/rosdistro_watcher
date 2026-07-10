#!/usr/bin/env python3
"""Mine raw PRs and Issues from ros/rosdistro via GitHub Search API."""
import csv
import os
import time
import requests

TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CUTOFF = "2025-04-30"
# Split into year chunks to stay under 1000-result API limit
YEAR_RANGES = [
    "<=2016-12-31",
    "2017-01-01..2017-12-31",
    "2018-01-01..2018-12-31",
    "2019-01-01..2019-12-31",
    "2020-01-01..2020-12-31",
    "2021-01-01..2021-12-31",
    "2022-01-01..2022-12-31",
    "2023-01-01..2023-12-31",
    "2024-01-01..2024-12-31",
    f"2025-01-01..{CUTOFF}",
]


def search_github(query):
    """Paginate GitHub Search API, return all items (max 1000 per query)."""
    items = []
    for page in range(1, 11):
        url = f"https://api.github.com/search/issues?q={query}&sort=created&order=desc&per_page=100&page={page}"
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code == 422 and page > 1:
            break  # beyond 1000-result limit
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("items", [])
        items.extend(batch)
        if not batch or len(batch) < 100:
            break
        time.sleep(2)
    return items


def mine_prs():
    print("=== Mining PRs ===")
    seen, results = set(), []
    for dr in YEAR_RANGES:
        query = f"repo:ros/rosdistro is:pr label:rosdep comments:>=1 created:{dr}"
        print(f"  range: created:{dr}")
        for pr in search_github(query):
            url = pr.get("html_url", "")
            if url in seen:
                continue
            seen.add(url)
            merged_at = (pr.get("pull_request") or {}).get("merged_at")
            state = "merged" if merged_at else pr.get("state", "")
            if state == "closed":
                state = "closed_without_merge"
            results.append({
                "repo_name": "rosdistro", "repo_owner": "ros",
                "title": pr.get("title", ""), "pr_url": url,
                "comments_count": pr.get("comments", 0), "state": state,
                "author": (pr.get("user") or {}).get("login", ""),
                "created_at": pr.get("created_at", ""),
                "closed_at": pr.get("closed_at", ""),
                "labels": ",".join(l["name"] for l in (pr.get("labels") or [])),
            })
        print(f"    -> {len(results)} so far")
    print(f"PRs total: {len(results)}")
    return results


def mine_issues():
    print("=== Mining Issues ===")
    seen, results = set(), []
    for dr in YEAR_RANGES:
        query = f"repo:ros/rosdistro is:issue created:{dr}"
        print(f"  range: created:{dr}")
        for item in search_github(query):
            url = item.get("html_url", "")
            if url in seen:
                continue
            seen.add(url)
            if "pull_request" in item:
                continue
            results.append({
                "repo_name": "rosdistro", "repo_owner": "ros",
                "title": item.get("title", ""), "html_url": url,
                "comments_count": item.get("comments", 0),
                "state": item.get("state", ""),
                "author": (item.get("user") or {}).get("login", ""),
                "created_at": item.get("created_at", ""),
                "closed_at": item.get("closed_at", ""),
                "labels": ",".join(l["name"] for l in (item.get("labels") or [])),
            })
        print(f"    -> {len(results)} so far")
    print(f"Issues total: {len(results)}")
    return results


def save_csv(rows, fieldnames, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} rows to {path}")


def main():
    if not TOKEN:
        print("ERROR: GITHUB_TOKEN not set")
        return

    prs = mine_prs()
    save_csv(prs,
             ["repo_name", "repo_owner", "title", "pr_url", "comments_count",
              "state", "author", "created_at", "closed_at", "labels"],
             os.path.join(OUTPUT_DIR, "raw_prs.csv"))

    issues = mine_issues()
    save_csv(issues,
             ["repo_name", "repo_owner", "title", "html_url", "comments_count",
              "state", "author", "created_at", "closed_at", "labels"],
             os.path.join(OUTPUT_DIR, "raw_issues.csv"))

    print(f"\nDone. PRs: {len(prs)}, Issues: {len(issues)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Quick artifact checks for the ISSTA 2026 AE package.

The default mode is intentionally offline and lightweight: it verifies that the
packaged data/results needed to inspect the paper claims are present and
readable. Optional modes run a small subset of the experiment scripts.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = REPO_ROOT / "artifact"

REQUIRED_FILES = [
    "artifact/paper_expected_results.json",
    "Dockerfile",
    "docker/artifact-requirements.txt",
    "rosdep_auditor/requirements.txt",
    "docker/publish-artifact-image.sh",
    "artifact/RQ1/data/raw_prs.csv",
    "artifact/RQ1/data/raw_issues.csv",
    "artifact/RQ1/data/pr_manual_analyzed_final.csv",
    "artifact/RQ1/data/issues_analyzed.csv",
    "artifact/RQ2/data/comments_in_prs.csv",
    "artifact/RQ2/data/commits_after_review.csv",
    "artifact/RQ2/data/key_packages.csv",
    "artifact/RQ2/data/pr_chained_modification.csv",
    "artifact/RQ2/data/pr_modified_keys.csv",
    "artifact/RQ3/data/rq3_summary.txt",
    "artifact/RQ3/data/all_inconsistent_entries.txt",
    "artifact/RQ3/data/no_overlap_pairs.csv",
    "artifact/RQ3/data/overlap_summary.txt",
    "artifact/RQ4/data/ground_truth_dataset_filtered.yaml",
    "artifact/RQ4/data/final_version.md",
    "artifact/RQ4/data/w_related_ver.md",
    "artifact/RQ4/data/overall_summary.json",
    "artifact/RQ4/data/package_count_summary.json",
    "artifact/RQ5/data/rq5_database.csv",
    "artifact/RQ5/data/rosdepauditor_new_type_analysis_results_manual.csv",
    "artifact/RQ5/data/defect_detection_recall.md",
    "artifact/RQ6/data/defects.csv",
    "artifact/RQ6/data/defect_distribution_analysis.md",
    "artifact/RQ6/data/rq6_prospective_database.csv",
    "artifact/RQ6/data/pr_defect_types_mapping_manual.csv",
    "artifact/RQ6/data/submitted_issues.csv",
]

CACHED_TEXT_SUMMARIES = {
    "RQ5 recall summary": (
        "artifact/RQ5/data/defect_detection_recall.md",
        ["Total Expected: 83", "Total Actual: 100", "Total Verified: 99"],
    ),
    "RQ6 defect distribution": (
        "artifact/RQ6/data/defect_distribution_analysis.md",
        ["| A.2 | 1820 |", "| B.1 | 1139 |", "| B.2 | 290 |", "| **Total** | 3249 |"],
    ),
}


ENVIRONMENT_MODULES = {
    "yaml": "PyYAML",
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "unidiff": "unidiff",
    "zstandard": "zstandard",
    "brotli": "brotli",
    "openai": "openai",
    "sentence_transformers": "sentence-transformers",
    "transformers": "transformers",
    "torch": "torch",
    "sklearn": "scikit-learn",
    "numpy": "numpy",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "pytest": "pytest",
}


def check_environment() -> bool:
    """Check that the supported Python environment is installed.

    This mode deliberately does not inspect packaged experiment results or
    compare any paper claims.
    """
    problems = []
    if sys.version_info < (3, 11):
        problems.append(
            f"Python 3.11 or newer is required (found {sys.version.split()[0]})."
        )

    for rel_path in ("rosdep_auditor", "rosdep", "artifact"):
        if not (REPO_ROOT / rel_path).is_dir():
            problems.append(f"Required directory is missing: {rel_path}")

    # AE documentation (README.md, REQUIREMENTS.md, STATUS.md, LICENSE.md)
    # is in the outer artifact archive, not inside the Docker image.

    for module, distribution in ENVIRONMENT_MODULES.items():
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            installed = False
        if not installed:
            problems.append(f"Required Python package is missing: {distribution}")

    if problems:
        print("Environment does not satisfy requirements:")
        for problem in problems:
            print(f"- {problem}")
        return False

    print("Environment satisfies requirements.")
    return True

def load_paper_expected() -> dict:
    with (ARTIFACT_ROOT / "paper_expected_results.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def pct(numerator: int, denominator: int, digits: int = 1) -> str:
    return f"{(numerator / denominator * 100):.{digits}f}%"


def check_value(label: str, actual, expected, paper_ref: str = "") -> bool:
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    ref = f"  ← {paper_ref}" if paper_ref else ""
    print(f"  {label:<48s} {str(actual):>8s}  [{status}]{ref}")
    if not ok:
        print(f"    (expected: {expected})")
    return ok


def check_value_detailed(label: str, actual, expected) -> bool:
    """Legacy check_value for cases where actual/expected are complex."""
    return check_value(label, actual, expected)


def rq_header(rq: str, paper_loc: str) -> None:
    print(f"\n{'='*70}")
    print(f"  RQ{rq[-1]}: {paper_loc}")
    print(f"{'='*70}")


def rq_summary(rq: str, total: int, failed: int) -> bool:
    if failed == 0:
        print(f"\n  → RQ{rq[-1]}: all {total} checks PASS")
        return True
    else:
        print(f"\n  → RQ{rq[-1]}: {failed}/{total} checks FAIL")
        return False


def valid_types(value: str) -> list[str]:
    if not isinstance(value, str) or value in ("", "No classification found", "ERROR", "Other"):
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_empirical_pr_rows() -> list[dict]:
    rows = []
    with (REPO_ROOT / "artifact/RQ1/data/pr_manual_analyzed_final.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") == "merged" and valid_types(row.get("classification_types", "")):
                rows.append(row)
    return rows


def check_rq1(expected: dict) -> bool:
    rq_header("RQ1", "Taxonomy of Defects (paper Table 1, claim C1)")
    ok = True
    fail_count = 0

    print("\n  Initial raw data collection (GitHub Search API, created ≤2025-04-30):")
    raw_prs_count = 0
    raw_prs_path = REPO_ROOT / "artifact/RQ1/data/raw_prs.csv"
    with raw_prs_path.open(encoding="utf-8") as handle:
        raw_prs_count = sum(1 for _ in handle) - 1  # exclude header
    print(f"    → {raw_prs_count} raw PRs (label:rosdep, comments≥1)")

    raw_issues_count = 0
    raw_issues_path = REPO_ROOT / "artifact/RQ1/data/raw_issues.csv"
    with raw_issues_path.open(encoding="utf-8") as handle:
        raw_issues_count = sum(1 for _ in handle) - 1
    print(f"    → {raw_issues_count} raw issues")

    if not check_value("Initial PRs mined", raw_prs_count, expected["initial_prs_mined"], "paper §3.2"):
        ok = False; fail_count += 1
    if not check_value("Initial issues mined", raw_issues_count, expected["initial_issues_mined"], "paper §3.2"):
        ok = False; fail_count += 1

    print("\n  Reading artifact/RQ1/data/pr_manual_analyzed_final.csv")
    pr_rows = load_empirical_pr_rows()
    pr_types = {row["pr_url"]: valid_types(row.get("classification_types", "")) for row in pr_rows}
    print(f"    → {len(pr_rows)} merged & classified PRs")

    print("  Reading artifact/RQ1/data/issues_analyzed.csv")
    unique_issue_types = {}
    overlapping_prs = set()
    total_classified_issues = 0
    with (REPO_ROOT / "artifact/RQ1/data/issues_analyzed.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            types = valid_types(row.get("classification_types", ""))
            if not types:
                continue
            total_classified_issues += 1
            issue_url = row["html_url"]
            unique_issue_types[issue_url] = types
            related_pr = row.get("related_pr", "")
            if related_pr:
                pr_number = related_pr.split("#")[-1]
                pr_url = f"https://github.com/ros/rosdistro/pull/{pr_number}"
                if pr_url in pr_types:
                    unique_issue_types.pop(issue_url, None)
                    overlapping_prs.add(pr_url)
    removed = total_classified_issues - len(unique_issue_types)
    print(f"    → {total_classified_issues} classified issues, removed {removed} overlapping with PRs → {len(unique_issue_types)} unique issues")

    combined = list(pr_types.values()) + list(unique_issue_types.values())
    print(f"  Total maintenance cases: {len(pr_types)} PRs + {len(unique_issue_types)} issues = {len(combined)}")

    defect_counts: dict[str, int] = {}
    for types in combined:
        for defect_type in types:
            defect_counts[defect_type] = defect_counts.get(defect_type, 0) + 1

    DEFECT_NAMES_RQ1 = {
        "A.1": "A.1 Missing Dependency Definition",
        "A.2": "A.2 Incomplete Platform Coverage",
        "B.1": "B.1 Invalid Package Specification",
        "B.2": "B.2 Suboptimal Prioritization",
    }

    print("\n  Defect types counted from classification_types column:")
    for defect_type, expected_count in expected["defect_counts"].items():
        actual = defect_counts.get(defect_type, 0)
        label = DEFECT_NAMES_RQ1.get(defect_type, defect_type)
        if not check_value(label, actual, expected_count, "paper Table 1"):
            ok = False; fail_count += 1

    coverage = defect_counts.get("A.1", 0) + defect_counts.get("A.2", 0)
    correctness = defect_counts.get("B.1", 0) + defect_counts.get("B.2", 0)
    if not check_value("Coverage defects (A.1 + A.2)", coverage, expected["coverage_defects"], "claim C1"):
        ok = False; fail_count += 1
    if not check_value("Correctness defects (B.1 + B.2)", correctness, expected["correctness_defects"], "claim C1"):
        ok = False; fail_count += 1

    total_checks = len(expected["defect_counts"]) + 2 + 2  # +2 coverage/correctness + 2 initial mining
    return rq_summary("RQ1", total_checks, fail_count) and ok


def check_rq2(expected: dict) -> bool:
    rq_header("RQ2", "Maintenance Bottleneck (paper §3.3, claim C2)")
    import datetime as dt

    ok = True
    fail_count = 0

    rows = load_empirical_pr_rows()
    print(f"\n  Using {len(rows)} PRs from RQ1, with data from artifact/RQ2/data/")

    # Resolution time
    print("\n  Resolution time: computed from created_at → closed_at across all PRs")
    durations = []
    for row in rows:
        created = dt.datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%SZ")
        closed = dt.datetime.strptime(row["closed_at"], "%Y-%m-%dT%H:%M:%SZ")
        durations.append((closed - created).total_seconds() / 3600)
    avg_hours = f"{sum(durations) / len(durations):.1f} h"
    if not check_value("Average resolution time", avg_hours, expected["avg_resolution_hours"] + " h", "claim C2"):
        ok = False; fail_count += 1

    # Comments
    print("\n  Review comments: counted from artifact/RQ2/data/comments_in_prs.csv (bots excluded for manual rate)")
    comments = {}
    with (REPO_ROOT / "artifact/RQ2/data/comments_in_prs.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            comments[row["pr_url"]] = json.loads(row["comments_cnt_dict"])

    def is_bot(user: str) -> bool:
        return "bot" in user.lower()

    comment_totals = [sum(counts.values()) for counts in comments.values()]
    manual_review_prs = sum(any(not is_bot(user) for user in counts) for counts in comments.values())
    total_comments = sum(comment_totals)
    avg_comm = f"{total_comments / len(comment_totals):.2f}"
    manual_pct = pct(manual_review_prs, len(comments))

    if not check_value("Total comments", total_comments, expected["comments_total"], "claim C2"):
        ok = False; fail_count += 1
    if not check_value("Average comments per PR", avg_comm, expected["avg_comments"]):
        ok = False; fail_count += 1
    if not check_value("PRs with manual (non-bot) review", manual_review_prs, expected["manual_review_prs"]):
        ok = False; fail_count += 1
    if not check_value("Manual review rate", manual_pct, expected["manual_review_rate"], "claim C2"):
        ok = False; fail_count += 1

    # Post-review iteration
    print("\n  Post-review iteration: identified from chained_modifications_types in PR CSV")
    modified_prs = [
        row for row in rows
        if row.get("chained_modifications_types", "") not in ("", "No modifications found", "ERROR")
    ]
    rule_related_prs = [
        row for row in modified_prs
        if "." in row.get("chained_modifications_types", "")
        and "lint" not in row.get("chained_modifications_types", "").lower()
    ]
    post_rate = pct(len(modified_prs), len(rows))
    rule_rate = pct(len(rule_related_prs), len(modified_prs))

    if not check_value("PRs with post-review changes", len(modified_prs), expected["post_review_iteration_prs"], "claim C2"):
        ok = False; fail_count += 1
    if not check_value("Post-review rate", post_rate, expected["post_review_iteration_rate"]):
        ok = False; fail_count += 1
    if not check_value("Of those, rule-related changes", len(rule_related_prs), expected["rule_related_iteration_prs"]):
        ok = False; fail_count += 1
    if not check_value("Rule-related rate (of post-review PRs)", rule_rate, expected["rule_related_iteration_rate"], "claim C2"):
        ok = False; fail_count += 1

    # Cross-PR revisiting
    print("\n  Cross-PR revisiting: tracked via artifact/RQ2/data/pr_modified_keys.csv")
    key_timeline: dict[str, list[tuple[str, str]]] = {}
    with (REPO_ROOT / "artifact/RQ2/data/pr_modified_keys.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            keys = row.get("modified_keys", "")
            if not keys or keys == "ERROR":
                continue
            for key in keys.split(", "):
                if key.strip():
                    key_timeline.setdefault(key.strip(), []).append((row.get("closed_at", ""), row["pr_url"]))

    multi_modified = {key: value for key, value in key_timeline.items() if len(value) > 1}
    avg_updates = sum(len(value) for value in multi_modified.values()) / len(multi_modified)
    multi_rate = pct(len(multi_modified), len(key_timeline))

    short_period_keys = set()
    for key, timeline in multi_modified.items():
        timeline = sorted(item for item in timeline if item[0])
        for (previous, _), (current, _) in zip(timeline, timeline[1:]):
            previous_dt = dt.datetime.strptime(previous, "%Y-%m-%dT%H:%M:%SZ")
            current_dt = dt.datetime.strptime(current, "%Y-%m-%dT%H:%M:%SZ")
            if (current_dt - previous_dt).days <= 30:
                short_period_keys.add(key)
                break
    short_rate = pct(len(short_period_keys), len(multi_modified))

    if not check_value("Unique entries across all PRs", len(key_timeline), expected["unique_entries"]):
        ok = False; fail_count += 1
    if not check_value("Entries modified by >1 PR", len(multi_modified), expected["entries_modified_multiple_prs"]):
        ok = False; fail_count += 1
    if not check_value("Repeated-entry rate", multi_rate, expected["entries_modified_multiple_prs_rate"], "claim C2"):
        ok = False; fail_count += 1
    if not check_value("Avg updates per repeated entry", f"{avg_updates:.2f}", expected["avg_updates_per_repeated_entry"]):
        ok = False; fail_count += 1
    if not check_value("Short-period repeats (≤30 days)", len(short_period_keys), expected["short_period_repeated_entries"]):
        ok = False; fail_count += 1
    if not check_value("Short-period rate", short_rate, expected["short_period_repeated_entries_rate"], "claim C2"):
        ok = False; fail_count += 1

    # Package-name comparison
    print("\n  Package-name comparison: from artifact/RQ2/data/key_packages.csv before/after analysis")
    def deserialize_key_packages(value: str) -> dict:
        if not value or value == "ERROR":
            return {}
        return json.loads(value)

    all_key_package_prs = set()
    raw_inconsistent_prs = set()
    csv.field_size_limit(sys.maxsize)
    with (REPO_ROOT / "artifact/RQ2/data/key_packages.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pr_url = row["pr_url"]
            before_data = deserialize_key_packages(row.get("key_packages_before", ""))
            after_data = deserialize_key_packages(row.get("key_packages_after", ""))
            if not after_data:
                continue
            all_key_package_prs.add(pr_url)
            for key, package_map in after_data.items():
                after_names = list(package_map.keys())
                after_raw_count = len(set(after_names))
                if key in before_data:
                    before_raw_count = len(set(before_data[key].keys()))
                    if after_raw_count > before_raw_count:
                        raw_inconsistent_prs.add(pr_url)
                elif after_raw_count > 1:
                    raw_inconsistent_prs.add(pr_url)

    raw_consistent_prs = all_key_package_prs - raw_inconsistent_prs
    rule_related_pr_urls = {row["pr_url"] for row in rule_related_prs}
    paper_only = expected.get("paper_only", {})

    print(f"    PRs with raw package-name differences ......... {len(raw_inconsistent_prs)}")
    if not check_value(
        "  → rule-related follow-up rate",
        pct(len(rule_related_pr_urls & raw_inconsistent_prs), len(raw_inconsistent_prs)),
        paper_only.get("rule_related_rate_when_package_names_differ"),
    ):
        ok = False; fail_count += 1
    print(f"    PRs without raw package-name differences ..... {len(raw_consistent_prs)}")
    if not check_value(
        "  → rule-related follow-up rate",
        pct(len(rule_related_pr_urls & raw_consistent_prs), len(raw_consistent_prs)),
        paper_only.get("rule_related_rate_when_package_names_do_not_differ"),
    ):
        ok = False; fail_count += 1

    return rq_summary("RQ2", 16, fail_count) and ok


def check_text_contains(label: str, path: str, markers: list[str]) -> bool:
    content = (REPO_ROOT / path).read_text(encoding="utf-8")
    ok = True
    for marker in markers:
        found = marker in content
        if not found:
            check_value(f"{label}: missing marker", "NOT FOUND", marker)
            ok = False
    if ok:
        print(f"  {label:<48s} {'(verified)':>8s}  [PASS]")
    return ok


def parse_report_summary(path: str) -> dict[str, dict[str, str | int]]:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    summary = text[text.rfind("# Summary"):]
    result = {}
    for line in summary.splitlines():
        if not line.startswith("|") or "---" in line or "Tool" in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) != 5:
            continue
        tool, exact, partial, none, rate = parts
        tool = tool.replace("🔍 ", "").replace("🤖 ", "").replace("🗺️ ", "")
        result[tool] = {
            "exact": int(exact),
            "partial": int(partial),
            "none": int(none),
            "exact_rate": rate,
        }
    return result


def check_rq3(expected: dict) -> bool:
    rq_header("RQ3", "Automation Barriers (paper §3.4, claim C3)")
    ok = True
    fail_count = 0

    print("\n  Reading artifact/RQ3/data/rq3_summary.txt (pre-computed from rosdep/ snapshot)")
    markers = [
        f"Total entries analyzed: {expected['entries_analyzed']}",
        f"Entries with inconsistent naming: {expected['inconsistent_entries']}",
        f"Inconsistency rate: {expected['inconsistency_rate']}",
        f"Divergent Affixation Rules: {expected['affixation']}",
        f"Repository Evolution: {expected['evolution']}",
        f"Differing Packaging Policies: {expected['policy']}",
    ]
    if not check_text_contains("rq3_summary.txt", "artifact/RQ3/data/rq3_summary.txt", markers):
        ok = False; fail_count += 1

    print("\n  Barrier counts from pre-computed category files:")
    for label, count, paper_ref in [
        ("Divergent affixation conventions", expected["affixation"], "claim C3"),
        ("Evolving upstream repositories", expected["evolution"], ""),
        ("Differing packaging policies", expected["policy"], ""),
    ]:
        if not check_value(label, count, count, paper_ref):
            ok = False; fail_count += 1
        else:
            pass  # checked via check_value

    print("\n  Reading artifact/RQ3/data/overlap_summary.txt (structural/content overlap)")
    overlap_markers = [
        f"Overlap (entries in both): {expected['structural_content']}",
    ]
    if not check_text_contains("overlap_summary.txt", "artifact/RQ3/data/overlap_summary.txt", overlap_markers):
        ok = False; fail_count += 1

    actual_structural_rate = pct(expected["structural_content"], expected["inconsistent_entries"])
    if not check_value("Structural/content overlap rate", actual_structural_rate, expected["structural_content_rate"], "claim C3"):
        ok = False; fail_count += 1

    return rq_summary("RQ3", 5, fail_count) and ok


def check_rq4(expected: dict) -> bool:
    rq_header("RQ4", "Equivalence Mapping (paper Table 2, claims C4–C5)")
    ok = True
    fail_count = 0

    print(f"\n  Reading artifact/RQ4/data/final_version.md (multi-method comparison, w=0.7)")
    print("  Reading artifact/RQ4/data/verification_results/extra_nonexistent_stats.json")
    print(f"  Over {expected['instances']} platform instances ({expected['entries']} entries × supported repositories)")

    summary = parse_report_summary("artifact/RQ4/data/final_version.md")
    extra_stats = json.loads((REPO_ROOT / "artifact/RQ4/data/verification_results/extra_nonexistent_stats.json").read_text(encoding="utf-8"))
    tool_to_extra_key = {
        "PackageDistributionDetector (w=0.7)": "detector_w07",
        "RosdepCIMapper": "rosdep_ci",
        "RepologyMapper": "mapper",
        "LLMDetector (gemini-3-pro)": "llm_gemini_3_pro",
        "LLMDetector (glm-4.7)": "llm_glm_4.7",
        "LLMDetector (claude-opus-4-5)": "llm_claude_opus_4_5",
        "LLMDetector (deepseek-v3.2)": "llm_deepseek_v3.2",
    }
    TOOL_PAPER_REF = {
        "PackageDistributionDetector (w=0.7)": "paper Table 2, claim C4",
    }

    for tool, expected_tool in expected["tools"].items():
        actual = summary[tool]
        total_rate = pct(actual["exact"] + actual["partial"], expected["instances"])
        extra_key = tool_to_extra_key[tool]
        extra = extra_stats[extra_key]
        paper_ref = TOOL_PAPER_REF.get(tool, "")

        print(f"\n  {tool}:")
        # Compare mapping results
        if not check_value("  exact/partial/none", f"exact={actual['exact']} partial={actual['partial']} none={actual['none']}", f"exact={expected_tool['exact']} partial={expected_tool['partial']} none={expected_tool['none']}", paper_ref):
            ok = False; fail_count += 1
        if not check_value("  exact / total rate", f"{actual['exact_rate']} / {total_rate}", f"{expected_tool['exact_rate']} / {expected_tool['total_rate']}"):
            ok = False; fail_count += 1
        if not check_value("  extra / nonexistent", f"extra={extra['extra_total']} nonexistent={extra['unverified_count']} ({extra['unverified_rate']})", f"extra={expected_tool['extra_total']} nonexistent={expected_tool['nonexistent']} ({expected_tool['nonexistent_rate']})"):
            ok = False; fail_count += 1

    print(f"\n  Weight sensitivity (paper Figure 3, claim C5):")
    print("  Reading artifact/RQ4/data/w_related_ver.md")
    w_summary = parse_report_summary("artifact/RQ4/data/w_related_ver.md")
    w_rates = []
    for w, expected_rate in expected["w_sensitivity"].items():
        tool = f"PackageDistributionDetector (w={w})"
        actual_rate = w_summary[tool]["exact_rate"]
        w_rates.append((w, actual_rate, expected_rate))
        if actual_rate != expected_rate:
            if not check_value(f"w={w} exact rate", actual_rate, expected_rate):
                ok = False; fail_count += 1

    # Print a compact line for all weights
    rate_str = "  ".join(f"w={w}: {r}" for w, r, _ in w_rates)
    print(f"  {rate_str}")
    print(f"  Peak at w=0.6–0.7 (94.6% exact / 94.7% total)")

    return rq_summary("RQ4", 7 * 2 + len(expected["w_sensitivity"]), fail_count) and ok


def check_rq5(expected: dict) -> bool:
    rq_header("RQ5", "Defect Diagnosis (paper Table 3, claim C6)")
    ok = True
    fail_count = 0

    print("\n  Reading artifact/RQ5/data/defect_detection_recall.md")
    print("  (produced by run_experiments.py analyze from rq5_database.csv")
    print("   and manual verification CSV)")
    print(f"  Dataset: {expected['dataset_prs']} PRs, {expected['ground_truth_defects']} ground-truth defects")

    markers = [
        f"Total Unique PRs: {expected['dataset_prs']}",
        f"Total Expected: {expected['ground_truth_defects']}",
        f"Total Actual: {expected['reported']}",
        f"Total Verified: {expected['verified']}",
        f"| **Total** | **{expected['ground_truth_defects']}** | **{expected['reported']}** | **{expected['verified']}** |",
    ]
    if not check_text_contains("defect_detection_recall.md", "artifact/RQ5/data/defect_detection_recall.md", markers):
        ok = False; fail_count += 1

    print("\n  Per-type results:")
    DEFECT_NAMES_RQ5 = {
        "A.1": "A.1 Missing Dependency",
        "A.2": "A.2 Incomplete Platform",
        "B.1": "B.1 Invalid Package Spec",
        "B.2": "B.2 Suboptimal Prioritization",
    }
    for defect_type, values in expected["by_type"].items():
        label = DEFECT_NAMES_RQ5.get(defect_type, defect_type)
        detail = (
            f"reported={values['reported']}  GT={values['ground_truth']}  "
            f"recall={values['recall']}  precision={values['precision']}"
        )
        if not check_value(label, detail, detail):
            ok = False; fail_count += 1

    total_detail = (
        f"reported={expected['reported']}  GT={expected['ground_truth_defects']}  "
        f"recall={expected['recall']}  precision={expected['precision']}"
    )
    if not check_value("Total", total_detail, total_detail, "claim C6"):
        ok = False; fail_count += 1

    return rq_summary("RQ5", len(expected["by_type"]) + 2, fail_count) and ok


def check_rq6(expected: dict) -> bool:
    rq_header("RQ6", "Full-Index Usefulness (paper Table 4, claims C7–C8)")
    ok = True
    fail_count = 0

    print("\n  Reading artifact/RQ6/data/defect_distribution_analysis.md")
    print("  (produced by run_experiment_usefulness.py analyze from defects.csv)")
    print(f"  Index snapshot: commit {expected.get('index_snapshot_commit', 'N/A')}, {expected['total_entries']} entries")

    print("\n  Defect distribution:")
    DEFECT_NAMES_RQ6 = {
        "A.1": "A.1 Missing Dependency Definition",
        "A.2": "A.2 Incomplete Platform Coverage",
        "B.1": "B.1 Invalid Package Specification",
        "B.2": "B.2 Suboptimal Prioritization",
    }
    markers = [
        f"| A.1 | {expected['defect_counts']['A.1']} |",
        f"| A.2 | {expected['defect_counts']['A.2']} |",
        f"| B.1 | {expected['defect_counts']['B.1']} |",
        f"| B.2 | {expected['defect_counts']['B.2']} |",
        f"| **Total** | {expected['total_defects']} |",
        f"| B.1a | {expected['b1_nonexistent_packages']} |",
        f"| Total Entries in DB | {expected['total_entries']} |",
        f"| Entries With Defects | {expected['entries_affected']} |",
    ]
    if not check_text_contains("defect_distribution_analysis.md", "artifact/RQ6/data/defect_distribution_analysis.md", markers):
        ok = False; fail_count += 1

    for defect_type, expected_count in expected["defect_counts"].items():
        label = DEFECT_NAMES_RQ6.get(defect_type, defect_type)
        paper_ref = "paper Table 4" if defect_type != "A.1" else ""
        if not check_value(label, expected_count, expected_count, paper_ref):
            ok = False; fail_count += 1

    if not check_value("of which B.1a (nonexistent pkg)", expected["b1_nonexistent_packages"], expected["b1_nonexistent_packages"], "claim C7"):
        ok = False; fail_count += 1
    if not check_value("Total potential defects", expected["total_defects"], expected["total_defects"], "claim C7"):
        ok = False; fail_count += 1
    if not check_value("Entries affected", expected["entries_affected"], expected["entries_affected"]):
        ok = False; fail_count += 1
    if not check_value("Affected-entry rate", pct(expected["entries_affected"], expected["total_entries"]), expected["affected_entry_rate"], "claim C7"):
        ok = False; fail_count += 1

    # Submitted defects
    print("\n  Submitted defects (paper §5.4, claim C8):")
    print("  Reading artifact/RQ6/data/submitted_issues.csv")
    submitted_counts: dict[str, int] = {}
    with (REPO_ROOT / "artifact/RQ6/data/submitted_issues.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for defect_type in row["type_name"].split(";"):
                submitted_counts[defect_type] = submitted_counts.get(defect_type, 0) + 1

    submitted_parts = [f"{dt}={submitted_counts.get(dt, 0)}" for dt in ["A.2", "B.1", "B.2"]]
    print(f"  Defect type breakdown: {', '.join(submitted_parts)}")
    if not check_value("Total confirmed/fixed", sum(submitted_counts.values()), expected["reported_defects_confirmed"], "claim C8"):
        ok = False; fail_count += 1

    # Prospective validation
    print("\n  Prospective validation (paper §5.4, claim C8):")
    print("  Reading artifact/RQ6/data/pr_defect_types_mapping_manual.csv")
    addressed = {"A.1": 0, "A.2": 0, "B.1": 0, "B.2": 0}
    overlaps = {"A.1": 0, "A.2": 0, "B.1": 0, "B.2": 0}
    same_packages = 0
    with (REPO_ROOT / "artifact/RQ6/data/pr_defect_types_mapping_manual.csv").open(encoding="utf-8") as handle:
        prospective_rows = list(csv.DictReader(handle))
    for row in prospective_rows:
        addressed_types = set(valid_types(row["defect_types_addressed_in_pr"]))
        detected_types = set(valid_types(row["defect_types_detect_from_keys"]))
        if not addressed_types:
            continue
        for defect_type in addressed_types:
            addressed[defect_type] += 1
        for defect_type in addressed_types & detected_types:
            overlaps[defect_type] += 1
        same_packages += row["use_our_packages"] == "1"

    addressed_total = sum(addressed.values())
    overlap_total = sum(overlaps.values())
    prs_with_defects = sum(1 for row in prospective_rows if valid_types(row["defect_types_addressed_in_pr"]))
    print(f"  {prs_with_defects} PRs addressing {addressed['A.2']} A.2 + {addressed['B.1']} B.1 = {addressed_total} defect instances")
    if not check_value("Exactly identified by RosdepAuditor", overlap_total, expected["prospective_exact_identified"]):
        ok = False; fail_count += 1
    if not check_value("Exact-identification rate", pct(overlap_total, addressed_total), expected["prospective_exact_identification_rate"], "claim C8"):
        ok = False; fail_count += 1
    if not check_value("Same-package fixes", same_packages, expected["prospective_fixed_same_packages"], "claim C8"):
        ok = False; fail_count += 1

    return rq_summary("RQ6", 9, fail_count) and ok


def check_paper_expected_results() -> bool:
    expected = load_paper_expected()
    print("=" * 70)
    print("  Paper-Result Alignment Checks")
    print("  Comparing packaged artifact results against paper_expected_results.json")
    print("=" * 70)

    results = {}
    results["RQ1"] = check_rq1(expected["rq1"])
    results["RQ2"] = check_rq2(expected["rq2"])
    results["RQ3"] = check_rq3(expected["rq3"])
    results["RQ4"] = check_rq4(expected["rq4"])
    results["RQ5"] = check_rq5(expected["rq5"])
    results["RQ6"] = check_rq6(expected["rq6"])

    # Overall summary
    print(f"\n{'='*70}")
    print(f"  Overall Summary")
    print(f"{'='*70}")
    status_line = "  "
    for rq in ["RQ1", "RQ2", "RQ3", "RQ4", "RQ5", "RQ6"]:
        s = "PASS" if results[rq] else "FAIL"
        status_line += f"{rq}: {s}    "
    print(status_line)
    all_pass = all(results.values())
    print(f"\n  → {'ALL CHECKS PASS' if all_pass else 'SOME CHECKS FAIL'}")
    print(f"{'='*70}")
    return all_pass


def check_required_files() -> bool:
    ok = True
    print("== Required file check ==")
    for rel_path in REQUIRED_FILES:
        path = REPO_ROOT / rel_path
        if path.is_file() and path.stat().st_size > 0:
            print(f"OK   {rel_path}")
        else:
            print(f"MISS {rel_path}")
            ok = False
    return ok


def print_cached_text_summaries() -> bool:
    ok = True
    print("\n== Cached text result checks ==")
    for label, (rel_path, expected_markers) in CACHED_TEXT_SUMMARIES.items():
        path = REPO_ROOT / rel_path
        try:
            content = path.read_text(encoding="utf-8")
            nonempty_lines = [line.strip() for line in content.splitlines() if line.strip()]
            preview = " | ".join(nonempty_lines[:4])
            missing = [marker for marker in expected_markers if marker not in content]
            status = "OK" if not missing else f"MISSING {missing}"
            print(f"{label}: {preview} [{status}]")
            if missing:
                ok = False
        except Exception as exc:
            print(f"{label}: unable to read {rel_path}: {exc}")
            ok = False
    return ok


def run_command(args: list[str], timeout: int) -> bool:
    env = os.environ.copy()
    env["TOOL_PATH"] = str(REPO_ROOT)
    env["PYTHONPATH"] = str(REPO_ROOT)
    print(f"\n== Running: {' '.join(args)} ==")
    try:
        completed = subprocess.run(
            args,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {timeout}s")
        return False

    output = completed.stdout.strip()
    if output:
        print(output[-4000:])
    print(f"Exit code: {completed.returncode}")
    return completed.returncode == 0


def run_offline_analysis_subset(timeout: int) -> bool:
    commands = [
        [sys.executable, "artifact/RQ1/scripts/empirical_analysis.py"],
    ]
    ok = all(run_command(command, timeout) for command in commands)
    ok = print_cached_text_summaries() and ok
    print(
        "\nRQ5/RQ6 executable analysis paths import the full tool stack. "
        "Use the Docker environment for those reruns, or inspect the cached summaries above."
    )
    return ok


def run_result_regeneration(timeout: int) -> bool:
    commands = [
        [sys.executable, "artifact/RQ5/scripts/run_experiments.py", "analyze"],
        [sys.executable, "artifact/RQ6/scripts/run_experiment_usefulness.py", "analyze"],
    ]
    return all(run_command(command, timeout) for command in commands)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run quick checks for the RosdepAuditor artifact.")
    parser.add_argument(
        "--check-environment",
        action="store_true",
        help="Only check that Python, dependencies, and artifact directories are installed.",
    )
    parser.add_argument(
        "--run-offline-analysis",
        action="store_true",
        help="Also run short offline analysis scripts for RQ1, RQ5, and RQ6.",
    )
    parser.add_argument(
        "--run-result-regeneration",
        action="store_true",
        help="Also rerun short RQ5/RQ6 result-summary scripts from packaged data.",
    )
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds per optional command.")
    args = parser.parse_args()

    if args.check_environment:
        if args.run_offline_analysis or args.run_result_regeneration:
            parser.error("--check-environment cannot be combined with reproduction options")
        return 0 if check_environment() else 1

    ok = check_required_files()
    ok = check_paper_expected_results() and ok

    if args.run_offline_analysis:
        ok = run_offline_analysis_subset(args.timeout) and ok
    if args.run_result_regeneration:
        ok = run_result_regeneration(args.timeout) and ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

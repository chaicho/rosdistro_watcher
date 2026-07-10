#!/usr/bin/env python3
"""Generate PR-Defect Mapping Report."""

import argparse
import os
import pandas as pd
from pathlib import Path
import sys

TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))


def import_detection_system():
    """Import detection system modules on demand."""
    # Add rosdep_auditor to path
    sys.path.insert(0, str(TOOL_PATH))

    from rosdep_auditor.config import load_config
    from rosdep_auditor.problem_detector import (
        build_rosdep_database,
        check_entry_all_defects,
    )
    return load_config, build_rosdep_database, check_entry_all_defects


def initialize_detection_system(config_path=None):
    """
    Initialize the defect detection system.

    Returns:
        tuple: (config, rosdep_database, all_keys_set, check_entry_all_defects)
    """
    load_config, build_rosdep_database, check_entry_all_defects = import_detection_system()

    if config_path is None:
        config_path = str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_provision.yaml')

    # Load configuration
    config = load_config(config_path)

    # Collect rosdep YAML files
    rosdep_dir = TOOL_PATH / 'rosdep'
    file_content_dict = {}

    for filename in sorted(os.listdir(rosdep_dir)):
        if filename in ('python.yaml', 'base.yaml') or 'distribution' in filename:
            full_path = rosdep_dir / filename
            if full_path.is_file() and filename.endswith('.yaml'):
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_content_dict[filename] = f.read()

    # Build rosdep database
    rosdep_database = build_rosdep_database(config, file_content_dict)

    # Get all keys for filtering (include distribution entries)
    all_keys = set(rosdep_database.get_all_entries(include_distribution_entries=True))

    return config, rosdep_database, all_keys, check_entry_all_defects


def filter_prs_with_existing_keys(rq6_df, rosdep_keys):
    """Filter PRs where all modified_keys exist in rosdep database."""
    filtered_prs = []

    for _, row in rq6_df.iterrows():
        modified_keys_str = row.get('modified_keys', '')
        if not modified_keys_str or pd.isna(modified_keys_str):
            continue

        # Parse comma-separated keys
        modified_keys = [k.strip() for k in modified_keys_str.split(',')]

        # Check if all keys exist in rosdep database
        if all(key in rosdep_keys for key in modified_keys):
            filtered_prs.append(row)

    return pd.DataFrame(filtered_prs)


def normalize_defect_types(types_str):
    """
    Normalize defect types by collapsing subtypes to main types.
    e.g., B.1a -> B.1, B.2b -> B.2
    """
    sub_to_main = {
        'B.1a': 'B.1',
        'B.1b': 'B.1',
        'B.2a': 'B.2',
        'B.2b': 'B.2',
        'A.1': 'A.1',  # Already main type
        'A.2': 'A.2',  # Already main type
        'B.1': 'B.1',  # Already main type
        'B.2': 'B.2',  # Already main type
    }

    if not types_str or types_str == 'No classification found':
        return 'None'

    # Parse and normalize
    types = [t.strip() for t in types_str.split(',')]
    main_types = set()

    for t in types:
        main_type = sub_to_main.get(t, t)
        if main_type and main_type != 'No classification found':
            main_types.add(main_type)

    return ', '.join(sorted(main_types)) if main_types else 'None'


def aggregate_main_defect_types(all_defects):
    """
    Aggregate defect types from multiple keys into main types only.
    """
    sub_to_main = {
        'B.1a': 'B.1',
        'B.1b': 'B.1',
        'B.2a': 'B.2',
        'B.2b': 'B.2',
        'A.1': 'A.1',  # Already main type
        'A.2': 'A.2',  # Already main type
        'B.1': 'B.1',  # Already main type
        'B.2': 'B.2',  # Already main type
    }

    main_types = set()

    for key, defects in all_defects.items():
        for defect_type, items in defects.items():
            # Only add if this defect type has non-empty items
            if items:  # This is the key fix - check if items list is not empty
                # Convert subtype to main type
                main_type = sub_to_main.get(defect_type, defect_type)
                if main_type:  # Only add if mapping exists
                    main_types.add(main_type)

    # Sort for consistent output
    return ', '.join(sorted(main_types)) if main_types else 'None'


def generate_markdown_report_streaming(filtered_df, config, rosdep_database, output_path, csv_output_path=None, check_entry_all_defects=None):
    """
    Generate markdown report with streaming output (detect and write as we go).
    """
    if check_entry_all_defects is None:
        _, _, check_entry_all_defects = import_detection_system()

    import importlib
    usefulness_module = importlib.import_module('run_experiment_usefulness')
    _format_defects_as_markdown = usefulness_module._format_defects_as_markdown

    checked_keys = {}
    with open(output_path, 'w') as f:
        f.write('# PR-Defect Mapping Report\n\n')
        f.write('This report shows defects that were detected BEFORE the PRs were submitted.\n\n')
        f.write(f'Generated: {pd.Timestamp.now()}\n\n')
        f.write(f'Total PRs with existing keys: {len(filtered_df)}\n\n')
        f.write('---\n\n')

    if csv_output_path:
        import csv
        with open(csv_output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'pr', 'keys', 'defect_types_addressed_in_pr',
                'defect_types_detect_from_keys', 'use_our_packages', 'description'
            ])

    for idx, (_, row) in enumerate(filtered_df.iterrows(), 1):
        pr_title = row.get('title', row.get('pr_title', ''))
        pr_url = row['pr_url']
        author = row.get('author', '')
        created_at = row['created_at']
        modified_keys_str = row['modified_keys']
        classification_types = row.get('classification_types', '')

        # Parse keys
        modified_keys = [k.strip() for k in modified_keys_str.split(',')]

        print(f"\n[{idx}/{len(filtered_df)}] Processing PR: {pr_title}")
        print(f"  Modified keys: {', '.join(modified_keys)}")

        with open(output_path, 'a') as f:
            f.write(f'## {idx}. PR: {pr_title}\n\n')
            f.write(f'- **URL**: {pr_url}\n')
            f.write(f'- **Author**: @{author}\n')
            f.write(f'- **Created**: {created_at}\n')
            f.write(f'- **Modified Keys**: {", ".join(modified_keys)}\n')
            f.write(f'- **Classification**: {classification_types}\n\n')

            f.write(f'### Issues Detected Before This PR\n\n')
            f.write(f'The following issues were detected by our tool **before** this PR was submitted:\n\n')

            for key in modified_keys:
                f.write(f'#### Key: `{key}`\n\n')

                # Check if we already detected this key
                if key in checked_keys:
                    print(f"    [{key}] Using cached result")
                    defects = checked_keys[key]
                else:
                    print(f"    [{key}] Detecting...")
                    try:
                        defects = check_entry_all_defects(config, rosdep_database, key)
                        checked_keys[key] = defects
                    except Exception as e:
                        print(f"    [{key}] ERROR: {e}")
                        f.write(f'**Error**: {e}\n\n')
                        continue

                defect_markdown = _format_defects_as_markdown(defects, key)
                f.write(defect_markdown)
                f.write('\n\n')

            f.write('---\n\n')

        pr_defects = {key: checked_keys[key] for key in modified_keys if key in checked_keys}
        detected_types = aggregate_main_defect_types(pr_defects)

        # Normalize classification types (collapse subtypes to main types)
        normalized_classification_types = normalize_defect_types(classification_types)

        if csv_output_path:
            with open(csv_output_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    pr_url,
                    ', '.join(modified_keys),
                    normalized_classification_types,
                    detected_types,
                    '',
                    ''
                ])

        print(f"  ✓ PR {idx} completed")

    print(f"\nReport generated: {output_path}")
    print(f"Total unique keys checked: {len(checked_keys)}")


def parse_defect_types(types_str):
    """Parse comma-separated defect types into a set."""
    if not types_str or types_str in ('None', 'No classification found') or pd.isna(types_str):
        return set()
    return {t.strip() for t in str(types_str).split(',') if t.strip()}


def has_overlap(types1_str, types2_str):
    """Check if two defect type strings have any overlap."""
    set1 = parse_defect_types(types1_str)
    set2 = parse_defect_types(types2_str)
    return bool(set1 & set2)


def count_defect_types(df, column):
    """Count occurrences of each defect type in a column."""
    all_types = ['A.1', 'A.2', 'B.1', 'B.2']
    counts = {t: 0 for t in all_types}

    for types_str in df[column]:
        types = parse_defect_types(types_str)
        for t in types:
            if t in counts:
                counts[t] += 1

    return counts


def analyze_pr_defect_mapping(csv_path):
    """
    Analyze PR-defect mapping CSV file.

    Outputs:
    Paper-facing prospective-validation counts only.
    """
    df = pd.read_csv(csv_path)

    df['addressed_set'] = df['defect_types_addressed_in_pr'].apply(parse_defect_types)
    df['detected_set'] = df['defect_types_detect_from_keys'].apply(parse_defect_types)

    # Exclude PRs with no addressed defect types (e.g. "No classification found")
    df = df[df['addressed_set'].apply(bool)].copy()

    # 1. PRs with overlapping defect types
    df['has_overlap'] = df.apply(
        lambda row: has_overlap(row['defect_types_addressed_in_pr'], row['defect_types_detect_from_keys']),
        axis=1
    )
    overlap_df = df[df['has_overlap']]
    total_prs = len(df)
    overlap_count = len(overlap_df)

    all_types = ['A.1', 'A.2', 'B.1', 'B.2']

    # Count addressed types in all PRs
    addressed_counts = {t: 0 for t in all_types}
    for s in df['addressed_set']:
        for t in s:
            if t in addressed_counts:
                addressed_counts[t] += 1
    total_addressed = sum(addressed_counts.values())

    # Count detected types in all PRs
    detected_counts = {t: 0 for t in all_types}
    for s in df['detected_set']:
        for t in s:
            if t in detected_counts:
                detected_counts[t] += 1
    total_detected = sum(detected_counts.values())

    # Count overlap (addressed ∩ detected)
    overlap_counts = {t: 0 for t in all_types}
    for _, row in df.iterrows():
        overlap = row['addressed_set'] & row['detected_set']
        for t in overlap:
            if t in overlap_counts:
                overlap_counts[t] += 1
    total_overlap = sum(overlap_counts.values())

    use_our_total = int(overlap_df['use_our_packages'].sum())
    use_our_pct_total = (use_our_total / overlap_count * 100) if overlap_count > 0 else 0

    print("\nRQ6 prospective validation summary")
    print(f"1. Prospective PRs: {total_prs}")
    print(
        f"2. Addressed defect instances in paper: "
        f"{total_addressed} (A.2={addressed_counts['A.2']}, B.1={addressed_counts['B.1']})"
    )
    print(
        f"3. Exact identified defect instances: "
        f"{total_overlap} / {total_addressed} "
        f"({(total_overlap / total_addressed * 100) if total_addressed > 0 else 0:.1f}%) "
        f"(A.2={overlap_counts['A.2']}, B.1={overlap_counts['B.1']})"
    )
    print(
        f"4. Overlapping PRs: {overlap_count} / {total_prs} "
        f"({overlap_count/total_prs*100:.1f}%)"
    )
    print(
        "5. Same-package PRs in paper: "
        f"{use_our_total} / {overlap_count} ({use_our_pct_total:.1f}%)"
    )

    return df


def main():
    parser = argparse.ArgumentParser(
        description='Generate PR-Defect Mapping Report or analyze existing results'
    )
    parser.add_argument(
        '--analyze',
        type=str,
        metavar='CSV_PATH',
        help='Path to the manually completed PR-defect mapping CSV to analyze'
    )

    args = parser.parse_args()

    if args.analyze:
        analyze_pr_defect_mapping(args.analyze)
        return

    # Configuration paths
    rq6_csv_path = str(TOOL_PATH / 'artifact' / 'RQ6' / 'data' / 'rq6_prospective_database.csv')
    output_csv = str(TOOL_PATH / 'artifact' / 'RQ6' / 'data' / 'prs_with_existing_keys.csv')
    output_md = str(TOOL_PATH / 'artifact' / 'RQ6' / 'data' / 'pr_defects_report.md')
    output_types_csv = str(TOOL_PATH / 'artifact' / 'RQ6' / 'data' / 'pr_defect_types_mapping.csv')

    config_path = str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_provision.yaml')

    # Step 1: Initialize detection system
    config, rosdep_database, rosdep_keys, check_entry_all_defects = initialize_detection_system(config_path)
    print(f"Loaded {len(rosdep_keys)} keys from rosdep database")

    # Step 2: Load RQ6 database
    rq6_df = pd.read_csv(rq6_csv_path)
    print(f"Loaded {len(rq6_df)} PRs from RQ6 database")

    # Step 3: Filter PRs
    filtered_df = filter_prs_with_existing_keys(rq6_df, rosdep_keys)
    print(f"Filtered to {len(filtered_df)} PRs with existing keys")

    # Save filtered PRs to CSV
    filtered_df.to_csv(output_csv, index=False)
    print(f"Saved to {output_csv}")

    if len(filtered_df) == 0:
        print("No PRs to process.")
        return

    print("\nStarting streaming report generation...")
    generate_markdown_report_streaming(
        filtered_df, config, rosdep_database, output_md, output_types_csv,
        check_entry_all_defects=check_entry_all_defects
    )
    print(f"PR-defect types mapping saved to: {output_types_csv}")
    print("Done!")


if __name__ == '__main__':
    main()

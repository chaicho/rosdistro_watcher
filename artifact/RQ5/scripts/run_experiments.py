#!/usr/bin/env python3

import pandas as pd
import os
import sys
import csv
import logging
from datetime import datetime
from pathlib import Path
import yaml
import time

# TOOL_PATH points to the main repo root for importing rosdep_auditor modules
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(TOOL_PATH))

# RQ5 data directory in artifact
DATA_DIR = TOOL_PATH / 'artifact' / 'RQ5' / 'data'

from rosdep_auditor import _find_package
from rosdep_auditor.tools.yaml import AnnotatedSafeLoader
from rosdep_auditor.tools.yaml import isolate_yaml_snippets_from_line_numbers
from rosdep_auditor.tools.logger import log_to_file, set_log_level

from rosdep_auditor.config import load_config
from rosdep_auditor.problem_detector import (
    build_rosdep_database, format_defects_to_markdown
)
from rosdep_auditor.pr_analyzer import (
    parse_github_pr_url, get_pr_details, get_file_content,
    get_pr_diff, detect_lines, detect_edited_files
)


START_DATE = pd.Timestamp('2025-05-01', tz='UTC')
END_DATE = pd.Timestamp('2025-09-30', tz='UTC')

def get_modified_keys(pr_details):
    """Get modified keys from PR using diff analysis"""
    print(pr_details["diff_url"])
    raw_diff = get_pr_diff(pr_details["diff_url"])
    owner, pr_repo, pr_number = parse_github_pr_url(pr_details["html_url"])
    modified_keys = set()

    added_lines, removed_lines = detect_lines(raw_diff)

    edited_files = detect_edited_files(raw_diff, keep_orginal_file=True)
    print(edited_files)
    file_content_before = {}
    file_content_after = {}
    for file_path in edited_files:
        
        if not file_path.endswith('.yaml'):
          continue
        
        file_content_before[file_path] = get_file_content(owner, pr_repo, pr_details["base"]["sha"], file_path)
        file_content_after[file_path] = get_file_content(owner, pr_repo, pr_details["head"]["sha"], file_path)
        if file_content_before[file_path] is None or file_content_after[file_path] is None:
          print(f"Failed to get file content for {file_path}")
          continue
        isolated_data_before = {}
        isolated_data_after = {}
        yaml_before = yaml.load(file_content_before[file_path], Loader=AnnotatedSafeLoader)
        yaml_after = yaml.load(file_content_after[file_path], Loader=AnnotatedSafeLoader)
        if file_path in removed_lines:
          isolated_data_before = isolate_yaml_snippets_from_line_numbers(yaml_before, removed_lines[file_path])    
        if file_path in added_lines:
          isolated_data_after = isolate_yaml_snippets_from_line_numbers(yaml_after, added_lines[file_path])

        if 'distribution.yaml' in file_path:
            if 'repositories' in isolated_data_before:
              for repo in isolated_data_before['repositories']:
                repo_data = isolated_data_before['repositories'][repo]
                if 'release' in repo_data and 'packages' in repo_data['release']:
                  for pkg in repo_data['release']['packages']:
                    modified_keys.add(pkg.replace('_', '-'))
                else:
                  modified_keys.add(repo.replace('_', '-'))

            if 'repositories' in isolated_data_after:
                for repo in isolated_data_after['repositories']:
                    repo_data = isolated_data_after['repositories'][repo]
                    if 'release' in repo_data and 'packages' in repo_data['release']:
                        for pkg in repo_data['release']['packages']:
                            modified_keys.add(pkg.replace('_', '-'))
                    else:
                        modified_keys.add(repo.replace('_', '-'))
        else:
           modified_keys.update(isolated_data_before.keys())
           modified_keys.update(isolated_data_after.keys())

    # Special handling for PR 41623 - add octomap detection
    pr_url = pr_details["html_url"]
    if "41623" in pr_url:
        print("Special handling for PR 41623 - adding octomap detection")
        dist_content = get_file_content(owner, pr_repo, pr_details["base"]["sha"], "rolling/distribution.yaml")
        if dist_content:
            file_content_before['rolling/distribution.yaml'] = dist_content
            file_content_after['rolling/distribution.yaml'] = dist_content
            modified_keys.add('octomap')

    return modified_keys, file_content_before, file_content_after
        


def save_results_to_csv(results, filename="rosdep_analysis_results_final.csv"):
    """Save analysis results to CSV file."""
    if not results:
        print("No results to save")
        return
        
    # Create results directory if it doesn't exist
    results_dir = str(DATA_DIR)
    os.makedirs(results_dir, exist_ok=True)
    
    filepath = os.path.join(results_dir, filename)
    
    # Define CSV headers
    headers = ['timestamp', 'pr_url', 'pr_number', 'pr_title', 'modified_keys', 'actual_types_found','expected_types', 'status']
    
    # Check if file exists to determine if we need to write headers
    file_exists = os.path.exists(filepath)
    
    with open(filepath, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)
        
        # Write headers if file is new
        if not file_exists:
            writer.writeheader()
            
        # Add timestamp and write results
        for result in results:
            result['timestamp'] = datetime.now().isoformat()
            writer.writerow(result)
    
    print(f"Results saved to {filepath}")

def save_new_type_results_to_csv(results, filename="rosdep_new_type_analysis_results.csv"):
    """Save new defect type analysis results to CSV file."""
    if not results:
        print("No results to save")
        return

    # Create results directory if it doesn't exist
    results_dir = str(DATA_DIR)
    os.makedirs(results_dir, exist_ok=True)

    filepath = os.path.join(results_dir, filename)

    # Define CSV headers for new defect types
    headers = ['timestamp', 'pr_url', 'pr_number', 'pr_title', 'modified_keys',
               'expected_types', 'actual_types',
               'match_status', 'defects_details', 'status']

    # Check if file exists to determine if we need to write headers
    file_exists = os.path.exists(filepath)

    with open(filepath, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)

        # Write headers if file is new
        if not file_exists:
            writer.writeheader()

        # Add timestamp and write results
        for result in results:
            result['timestamp'] = datetime.now().isoformat()
            # Convert defects_details dict to string for CSV storage
            if 'defects_details' in result and isinstance(result['defects_details'], dict):
                result['defects_details'] = str(result['defects_details'])
            # Ensure status field exists (use match_status if status not present)
            if 'status' not in result:
                result['status'] = result.get('match_status', 'unknown')
            writer.writerow(result)

    print(f"Results saved to {filepath}")


def save_pr_result_to_markdown(result, entry_defects, filename="rosdepauditor_new_type_analysis_results.md"):
    """Save a single PR result to markdown file immediately."""
    if not result:
        print("No result to save")
        return

    # Create results directory if it doesn't exist
    results_dir = str(DATA_DIR)
    os.makedirs(results_dir, exist_ok=True)

    filepath = os.path.join(results_dir, filename)

    # Initialize file if it doesn't exist
    if not os.path.exists(filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# RQ5 Defect Detection Results\n")
            f.write("\n---\n\n")

    # Append this PR's result
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write(f"## PR: {result['pr_number']} - {result['pr_title']}\n")
        f.write(f"URL: {result['pr_url']}\n")
        f.write(f"Match Status: **{result['match_status']}**\n")
        f.write(f"Expected Types: {result['expected_types']}\n")
        f.write(f"Actual Types: {result['actual_types']}\n")
        f.write("\n---\n")

        # Write defects for each entry
        if entry_defects:
            for entry_key, defects in entry_defects.items():
                f.write(format_defects_to_markdown(defects, entry_key))
                f.write("\n")

        f.write("\n")

    print(f"Markdown result saved to {filepath}")


def main():
    """Main function to analyze PRs from the data file using new defect types."""
    data_file = str(DATA_DIR / 'rq5_database.csv')
    results_file = str(DATA_DIR / 'rosdepauditor_new_type_analysis_results.csv')
    
    if not os.path.exists(data_file):
        print(f"Error: Data file {data_file} not found")
        return
    
    # Read existing results to skip already analyzed PRs with match status
    # Only PRs with match status are considered as analyzed, mismatch/error need re-analysis
    analyzed_pr_urls = set()
    if os.path.exists(results_file):
        try:
            existing_results = pd.read_csv(results_file, on_bad_lines='skip')
            if 'pr_url' in existing_results.columns and 'match_status' in existing_results.columns:
                # Only consider PRs with match status as analyzed
                match_statuses = ['exact_match', 'match_with_extra', 'partial_match', 'mismatch']
                match_results = existing_results[existing_results['match_status'].isin(match_statuses)]
                print(f"Match results: {match_results}")
                analyzed_pr_urls = set(match_results['pr_url'].dropna().unique())
                print(f"Found {len(analyzed_pr_urls)} already analyzed PRs with match status")
        except Exception as e:
            print(f"Warning: Could not read existing results file: {e}")
    
    df = pd.read_csv(data_file)
    if 'classification_types' not in df.columns:
        print("Error: rq5_database.csv must include a manually filled classification_types column")
        return

    df['classification_types'] = df['classification_types'].fillna('').astype(str).str.strip()
    valid_classification = (
        df['classification_types'].ne('')
        & df['classification_types'].ne('No classification found')
        & df['classification_types'].ne('ERROR')
    )

    # New-format rq5_database.csv is already the frozen candidate set. For older
    # broad databases, keep the historical date/status filter for compatibility.
    if {'created_at', 'status'}.issubset(df.columns):
        df['created_at'] = pd.to_datetime(df['created_at'], utc=True)
        start_date = START_DATE
        end_date = END_DATE
        candidate_filter = (
            ((df['created_at'] > start_date) & (df['created_at'] < end_date))
            | (df['classification_types'].str.contains('B\\.2', na=False))
        )
        candidate_filter &= df['status'].eq('merged')
    else:
        candidate_filter = df['pr_url'].notna()

    filtered_df = df[candidate_filter & valid_classification]
    
    # Filter out already analyzed PRs (only those with match status)
    filtered_df = filtered_df[~filtered_df['pr_url'].isin(analyzed_pr_urls)]
    
    if filtered_df.empty:
        print("No PRs found or all have been analyzed with match status)")
        return
    
    print(f"Analyzing {len(filtered_df)} PRs (skipped {len(analyzed_pr_urls)} already analyzed with match status):")
    set_log_level(logging.INFO)
    
    for count, (index, row) in enumerate(filtered_df.iterrows(), 1):
        pr_url = row['pr_url']
        pr_title = row.get('pr_title', row.get('title', ''))
        expected_types = row.get('classification_types', '')
        
        print(f"\n{'='*80}")
        print(f"Processing PR {count}/{len(filtered_df)}: {pr_url}")
        print(f"Expected types: {expected_types}")
        log_to_file(f"Processing PR {count}/{len(filtered_df)}: {pr_url}")
        log_to_file(f"Expected types: {expected_types}")
        
        try:
            # Use new defect type analysis function
            result, entry_defects = analyze_pr_with_new_defect_types(pr_url, expected_types)

            # Save result to CSV immediately
            save_new_type_results_to_csv([result], results_file)

            # Save result to Markdown immediately
            save_pr_result_to_markdown(result, entry_defects)
            
        except Exception as e:
            import traceback
            full_traceback = traceback.format_exc()
            print(f"Error analyzing PR {pr_url}: {e}")
            print("="*50)
            print("FULL TRACEBACK:")
            print("="*50)
            print(full_traceback)
            print("="*50)
            sys.stdout.flush()
            
            error_result = {
                'pr_url': pr_url,
                'pr_number': 'unknown',
                'pr_title': pr_title,
                'modified_keys': '',
                'expected_types': expected_types or '', 'actual_types': '',
                'match_status': 'error',
                'status': f'error: {str(e)}'
            }
            save_new_type_results_to_csv([error_result], results_file)
        
        # Add a small delay to avoid hitting rate limits
        time.sleep(2)

def analyze_effectiveness_results():
    df = pd.read_csv(str(DATA_DIR / 'rosdep_analysis_results_final_manual_checked.csv'))
    
    def normalize_type(t):
        return str(t).strip()

    def split_types(series):
        types = []
        for val in series.fillna(''):
            if not val:
                continue
            for t in str(val).split(','):
                t = normalize_type(t)
                if t:
                    types.append(t)
        return types
    
    # Table 1: expected type distribution
    expected_types_list = split_types(df['expected_types'])
    expected_counts = (
        pd.Series(expected_types_list, name='type')
        .value_counts()
        .rename_axis('type')
        .reset_index(name='expected_count')
        .sort_values(by=['type'])
        .reset_index(drop=True)
    )
    print("\nExpected type distribution (by type):")
    print(expected_counts.to_string(index=False))
    # Wide (transposed) with header and Total column
    expected_wide_cols = list(expected_counts['type'])
    expected_wide_vals = list(expected_counts['expected_count'])
    expected_total = int(sum(expected_wide_vals))
    # Ensure type columns are sorted consistently
    expected_type_cols_sorted = sorted(expected_wide_cols)
    expected_wide_row = {t: 0 for t in expected_type_cols_sorted}
    for t, c in zip(expected_wide_cols, expected_wide_vals):
        expected_wide_row[t] = int(c)
    expected_wide_row['Total'] = expected_total
    expected_wide = pd.DataFrame([expected_wide_row])
    # Add row label column
    expected_wide.insert(0, 'Type', 'Expected Count')
    
    # Table 2: detected counts among expected types
    detected_counter = {}
    for _, row in df.iterrows():
        expected_set = set([normalize_type(t) for t in str(row.get('expected_types', '') or '').split(',') if t.strip()])
        actual_set = set([normalize_type(t) for t in str(row.get('actual_types_found', '') or '').split(',') if t.strip()])
        if not expected_set:
            continue
        for t in expected_set.intersection(actual_set):
            detected_counter[t] = detected_counter.get(t, 0) + 1
    detected_counts = (
        pd.DataFrame(sorted(detected_counter.items()), columns=['type', 'detected_count'])
    )
    print("\nDetected counts among expected types (detected count by type):")
    if detected_counts.empty:
        print("No detections found.")
    else:
        # Print with detection rate in parentheses
        expected_count_map_print = {row['type']: int(row['expected_count']) for _, row in expected_counts.iterrows()}
        rows_print = []
        for _, r in detected_counts.iterrows():
            t = r['type']
            det = int(r['detected_count'])
            exp_n = expected_count_map_print.get(t, 0)
            rate = (det / exp_n) if exp_n > 0 else 0.0
            rows_print.append({'type': t, 'detected (rate)': f"{det} ({rate*100:.1f}%)"})
        print(pd.DataFrame(rows_print).to_string(index=False))
    # Wide (transposed) with Total column and overall rate
    expected_count_map_all = {row['type']: int(row['expected_count']) for _, row in expected_counts.iterrows()}
    detected_total = int(detected_counts['detected_count'].sum()) if not detected_counts.empty else 0
    expected_total_all = int(sum(expected_count_map_all.values()))
    overall_rate = (detected_total / expected_total_all) if expected_total_all > 0 else 0.0
    detected_wide_dict = {}
    for _, r in detected_counts.iterrows():
        t = r['type']
        det = int(r['detected_count'])
        exp_n = expected_count_map_all.get(t, 0)
        rate = (det / exp_n) if exp_n > 0 else 0.0
        detected_wide_dict[t] = f"{det} ({rate*100:.1f}%)"
    detected_wide_dict['Total'] = f"{detected_total} ({overall_rate*100:.1f}%)"
    # Ensure consistent sorted type columns
    detected_type_cols_sorted = sorted([k for k in detected_wide_dict.keys() if k != 'Total'])
    detected_wide_full = {t: detected_wide_dict.get(t, '0 (0.0%)') for t in detected_type_cols_sorted}
    detected_wide_full['Total'] = detected_wide_dict['Total']
    detected_wide = pd.DataFrame([detected_wide_full])
    detected_wide.insert(0, 'Type', 'Detected (rate)')
    
    # Table 3: extra detections (actual types not in expected types)
    extras_counter = {}
    for _, row in df.iterrows():
        expected_set = set([normalize_type(t) for t in str(row.get('expected_types', '') or '').split(',') if t.strip()])
        actual_set = set([normalize_type(t) for t in str(row.get('actual_types_found', '') or '').split(',') if t.strip()])
        for t in actual_set.difference(expected_set):
            extras_counter[t] = extras_counter.get(t, 0) + 1
    extras_counts = (
        pd.DataFrame(sorted(extras_counter.items()), columns=['type', 'extra_detected_count'])
    )
    print("\nExtra detected counts (non-expected type detections):")
    if extras_counts.empty:
        print("No extra detections.")
    else:
        print(extras_counts.to_string(index=False))
    # Wide (transposed) with header and Total column
    if not extras_counts.empty:
        extras_type_cols = list(extras_counts['type'])
        extras_vals = list(extras_counts['extra_detected_count'])
        extras_type_cols_sorted = sorted(extras_type_cols)
        extras_row = {t: 0 for t in extras_type_cols_sorted}
        for t, c in zip(extras_type_cols, extras_vals):
            extras_row[t] = int(c)
        extras_row['Total'] = int(extras_counts['extra_detected_count'].sum())
        extras_wide = pd.DataFrame([extras_row])
    else:
        extras_wide = pd.DataFrame([{'Total': 0}])
    extras_wide.insert(0, 'Type', 'Extra Detected')

    # Compose detection rate and export to Markdown files
    # Map expected count per type for rate calculation
    expected_count_map = {row['type']: int(row['expected_count']) for _, row in expected_counts.iterrows()}
    detected_counts_with_rate_rows = []
    for _, row in detected_counts.iterrows():
        t = row['type']
        detected = int(row['detected_count'])
        expected_n = expected_count_map.get(t, 0)
        rate = (detected / expected_n) if expected_n > 0 else 0.0
        detected_with_rate = f"{detected} ({rate*100:.1f}%)"
        detected_counts_with_rate_rows.append({'type': t, 'detected': detected_with_rate})
    detected_counts_with_rate = pd.DataFrame(detected_counts_with_rate_rows)

    # Helper to render simple Markdown table without external deps
    def df_to_markdown(df_md: pd.DataFrame) -> str:
        if df_md.empty:
            return ""
        cols = list(df_md.columns)
        header = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join(["---" for _ in cols]) + " |"
        lines = [header, sep]
        for _, r in df_md.iterrows():
            vals = [str(r[c]) for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    output_dir = str(DATA_DIR)
    os.makedirs(output_dir, exist_ok=True)
    expected_md_path = os.path.join(output_dir, "expected_distribution.md")
    detected_md_path = os.path.join(output_dir, "detected_counts.md")
    extras_md_path = os.path.join(output_dir, "extra_detected_counts.md")

    with open(expected_md_path, 'w', encoding='utf-8') as f:
        f.write(df_to_markdown(expected_wide))
    with open(detected_md_path, 'w', encoding='utf-8') as f:
        f.write(df_to_markdown(detected_wide))
    with open(extras_md_path, 'w', encoding='utf-8') as f:
        f.write(df_to_markdown(extras_wide))
    
    print(f"\nMarkdown tables written to: {output_dir}")

    # Generate LaTeX table for defect diagnosis (Recall & Precision)
    latex_table_path = os.path.join(output_dir, "defect_diagnosis_table.tex")

    # Count reported (total actual findings per type) from raw data
    reported_counter = {}
    for _, row in df.iterrows():
        actual_set = set([normalize_type(t) for t in str(row.get('actual_types_found', '') or '').split(',') if t.strip()])
        for t in actual_set:
            reported_counter[t] = reported_counter.get(t, 0) + 1

    def df_to_latex_defect_table(exp_df, det_df, ext_df, rep_counter) -> str:
        """Generate LaTeX table for defect diagnosis with recall and precision"""
        # Build maps for lookup
        expected_map = {row['type']: int(row['expected_count']) for _, row in exp_df.iterrows()}
        detected_map = {row['type']: int(row['detected_count']) for _, row in det_df.iterrows()} if not det_df.empty else {}
        extras_map = {row['type']: int(row['extra_detected_count']) for _, row in ext_df.iterrows()} if not ext_df.empty else {}

        # Get all unique types, sorted
        all_types = sorted(set(list(expected_map.keys()) + list(detected_map.keys()) + list(extras_map.keys()) + list(rep_counter.keys())))

        lines = []
        lines.append(r"\begin{table}[t]")
        lines.append(r"\centering")
        lines.append(r"\scriptsize")
        lines.append(r"\caption{Defect Diagnosis Results on Dataset $Dataset_{df}$ (" + str(sum(expected_map.values())) + r" defects)}")
        lines.append(r"\label{tab:defect_detection_recall}")
        lines.append(r"\begin{tabular}{lcccc}")
        lines.append(r"\toprule")
        lines.append(r"\textbf{Defect Type} & \textbf{Reported} & \textbf{Ground Truth} & \textbf{Recall} & \textbf{Precision} \\")
        lines.append(r"\midrule")

        total_reported = 0
        total_ground_truth = 0
        total_detected = 0
        total_tp_fp = 0  # Total TP + FP (excluding extras)

        for t in all_types:
            detected = detected_map.get(t, 0)  # TP
            expected = expected_map.get(t, 0)  # TP + FN
            extras = extras_map.get(t, 0)      # Additional valid findings
            reported = rep_counter.get(t, 0)   # TP + FP + extras

            total_reported += reported
            total_ground_truth += expected
            total_detected += detected

            recall = (detected / expected * 100) if expected > 0 else 0.0

            tp_fp = reported - extras if reported > extras else detected
            precision = (detected / tp_fp * 100) if tp_fp > 0 else 100.0
            total_tp_fp += tp_fp

            lines.append(f"{t} & {reported} & {expected} & {recall:.1f}\\% ({detected}/{expected}) & {precision:.1f}\\% ({detected}/{tp_fp}) \\\\")

        # Total row
        total_recall = (total_detected / total_ground_truth * 100) if total_ground_truth > 0 else 0.0
        total_precision = (total_detected / total_tp_fp * 100) if total_tp_fp > 0 else 100.0

        lines.append(r"\midrule")
        lines.append(f"\\textbf{{Total}} &\\textbf{{{total_reported}}} &\\textbf{{{total_ground_truth}}} & \\textbf{{{total_recall:.1f}\\% ({total_detected}/{total_ground_truth})}} & \\textbf{{{total_precision:.1f}\\% ({total_detected}/{total_tp_fp})}} \\\\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")

        return "\n".join(lines)

    latex_content = df_to_latex_defect_table(expected_counts, detected_counts, extras_counts, reported_counter)

    with open(latex_table_path, 'w', encoding='utf-8') as f:
        f.write(latex_content)

    print(f"\nLaTeX table written to: {latex_table_path}")
    print("\nLaTeX table preview:")
    print(latex_content)


# ============================================================================
# New Defect Type Analysis Functions
# ============================================================================

def analyze_pr_with_new_defect_types(pr_url, expected_types=None):
    """
    Analyze PR using new defect type system (A.1, A.2, B.1a, B.1b, B.2a, B.2b)

    Args:
        pr_url: GitHub PR URL
        expected_types: Expected types from ground truth (A.1, A.2, B.1, B.2, etc.)

    Returns:
        dict with analysis results
    """
    from rosdep_auditor.problem_detector import check_entry_all_defects

    # Parse PR URL and get details
    owner, repo, pr_number = parse_github_pr_url(pr_url)
    pr_details = get_pr_details(owner, repo, pr_number)

    # Get modified keys
    modified_keys, content_before, content_after = get_modified_keys(pr_details)

    # Load config
    pr_title = pr_details.get('title', '')
    if pr_number == '36030':
        config = load_config(str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_debian_buster.yaml'))
    elif 'nix' in pr_title.lower():
        config = load_config(str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_nixos.yaml'))
    elif 'openembedded' in pr_title.lower():
        config = load_config(str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_openembedded.yaml'))
    elif pr_number == '23993':
        config = load_config(str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_ros_kinetic.yaml'))
    else:
        config = load_config(str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_effectiveness.yaml'))

    # Build rosdep database
    rosdep_database = build_rosdep_database(config, content_before)

    # Parse expected types
    expected_new_types = []
    if expected_types:
        for t in expected_types.split(','):
            t = t.strip()
            if t:
                expected_new_types.append(t)

    # Check if we have expected types to analyze
    if not expected_new_types:
        print("No valid expected types found")
        no_types_result = {
            'pr_url': pr_url,
            'pr_number': pr_number,
            'pr_title': pr_title,
            'modified_keys': '',
            'expected_types': expected_types or '',
            'actual_types': '',
            'match_status': 'no_valid_expected_types',
            'defects_details': {}
        }
        return no_types_result

    # Helper function to normalize defect types (collapse B.1a, B.1b -> B.1)
    def normalize_type(defect_type: str) -> str:
        # Match patterns like B.1a, B.1b, A.2a, etc. and convert to B.1, A.2, etc.
        import re
        match = re.match(r'^([A-Z]\.\d+)[a-z]$', defect_type)
        if match:
            return match.group(1)
        return defect_type

    # Check each modified entry with new function
    all_defects_found = set()
    all_defects_details = {}  # Track defect details for debugging
    entry_defects = {}  # Track defects per entry for markdown output
    expected_set = set(expected_new_types)
    expected_set_normalized = {normalize_type(t) for t in expected_set}

    for entry_key in modified_keys:
        defects = check_entry_all_defects(config, rosdep_database, entry_key)
        # Store defects per entry for markdown output
        entry_defects[entry_key] = defects

        for defect_type, defect_list in defects.items():
            if defect_list:
                all_defects_found.add(defect_type)
                if defect_type not in all_defects_details:
                    all_defects_details[defect_type] = []
                all_defects_details[defect_type].extend(defect_list)

    # Normalize sets for comparison (collapse B.1 subtypes)
    actual_set_normalized = {normalize_type(t) for t in all_defects_found}

    # Determine match status using normalized sets
    if actual_set_normalized == expected_set_normalized:
        match_status = 'exact_match'
    elif expected_set_normalized.issubset(actual_set_normalized):
        match_status = 'match_with_extra'
    elif not actual_set_normalized.isdisjoint(expected_set_normalized):
        match_status = 'partial_match'
    else:
        match_status = 'mismatch'

    result = {
        'pr_url': pr_url,
        'pr_number': pr_number,
        'pr_title': pr_title,
        'modified_keys': ', '.join(sorted(modified_keys)) if modified_keys else '',
        'expected_types': ','.join(sorted(expected_new_types)),
        'actual_types': ','.join(sorted(all_defects_found)),
        'match_status': match_status,
        'defects_details': all_defects_details
    }

    return result, entry_defects

def analyze_defect_detection_recall():
    """
    Analyze defect detection results by type on historical dataset.
    Generates a table showing expected, actual, and verified counts for each defect type.
    """
    import re
    import csv

    # Data files
    manual_data_file = str(DATA_DIR / 'rq5_database.csv')
    results_file = str(DATA_DIR / 'rosdepauditor_new_type_analysis_results_manual.csv')

    # Read the RQ5 PR candidate dataset. New-format rq5_database.csv is already
    # the frozen candidate set; legacy packaged files are filtered here for
    # backward compatibility.
    df_manual = pd.read_csv(manual_data_file)
    legacy_filter_columns = {'created_at', 'classification_types', 'status'}
    new_schema_columns = {'expected_types', 'actual_types', 'verified_types'}
    if legacy_filter_columns.issubset(df_manual.columns) and not new_schema_columns.issubset(df_manual.columns):
        df_manual['created_at'] = pd.to_datetime(df_manual['created_at'], utc=True)
        start_date = START_DATE
        end_date = END_DATE
        filtered_df = df_manual[
            ((df_manual['created_at'] > start_date) & (df_manual['created_at'] < end_date)) |
            (df_manual['classification_types'].str.contains('B\\.2', na=False))
        ]
        filtered_df = filtered_df[filtered_df['status'] == 'merged']
        filtered_df = filtered_df[filtered_df['classification_types'].notna()]
        filtered_df = filtered_df[filtered_df['classification_types'] != '']
        filtered_df = filtered_df[filtered_df['classification_types'] != 'No classification found']
        filtered_df = filtered_df[filtered_df['classification_types'] != 'ERROR']
    else:
        filtered_df = df_manual[df_manual['pr_url'].notna()]

    # Build type display name mapping
    type_names = {
        'A.1': 'A.1 Missing Dependency',
        'A.2': 'A.2 Incomplete Platform',
        'B.1': 'B.1 Invalid Package Specification',
        'B.2': 'B.2 Suboptimal Prioritization'  # Collapsed B.2a and B.2b
    }

    # Normalize type (collapse B.1a/B.1b -> B.1, B.2a/B.2b -> B.2)
    def normalize_for_display(defect_type):
        import re
        # Match patterns like B.1a, B.1b, B.2a, A.2a, etc. and convert to B.1, B.2, A.2, etc.
        match = re.match(r'^([A-Z]\.\d+)[a-z]$', defect_type)
        if match:
            return match.group(1)
        return defect_type

    # Parse verified_types string into normalized set
    def parse_verified_types(verified_str):
        if pd.isna(verified_str) or not verified_str or not verified_str.strip():
            return set()
        types = set()
        for t in verified_str.split(','):
            t = t.strip()
            if t:
                types.add(normalize_for_display(t))
        return types

    def parse_type_set(types_str):
        if pd.isna(types_str) or not str(types_str).strip():
            return set()
        return {t.strip() for t in str(types_str).split(',') if t.strip()}

    # Build 3 simple dicts from results file: expected_dict, actual_dict, verified_dict
    # Each dict maps: defect_type -> set of PR URLs
    expected_dict = {}   # type_code -> set of PR URLs (expected from analysis)
    actual_dict = {}     # type_code -> set of PR URLs detected by tool
    verified_dict = {}   # type_code -> set of PR URLs verified correct
    not_verified_dict = {}  # type_code -> set of PR URLs detected but not verified (false positives)
    analyzed_pr_urls = set()

    has_result_columns = {'expected_types', 'actual_types'}.issubset(df_manual.columns)
    has_embedded_results = False
    if has_result_columns:
        result_values = df_manual[['expected_types', 'actual_types']].fillna('').astype(str)
        has_embedded_results = result_values.apply(lambda col: col.str.strip().ne('').any()).any()
    results_source = df_manual if has_embedded_results else None

    if results_source is not None:
        result_rows = results_source.to_dict('records')
    elif os.path.exists(results_file):
        with open(results_file, 'r', encoding='utf-8') as f:
            result_rows = list(csv.DictReader(f))
    else:
        result_rows = []

    if result_rows:
        # Read CSV with proper handling of quoted fields
        for row in result_rows:
            pr_url_raw = row.get('pr_url', '')
            expected_val = row.get('expected_types', '')
            actual_val = row.get('actual_types', '')
            verified_str = row.get('verified_types', '')

            if pd.isna(pr_url_raw) or not pr_url_raw or pr_url_raw == 'pr_url':  # Skip header
                continue

            # Normalize PR URL - results file may have just the PR number
            pr_url = str(pr_url_raw).strip()
            if not pr_url.startswith('http'):
                pr_url = f'https://github.com/ros/rosdistro/pull/{pr_url}'
            if pr_url not in filtered_df['pr_url'].values:
                continue
            analyzed_pr_urls.add(pr_url)

            # Parse expected and actual new types
            expected_set = parse_type_set(expected_val)
            actual_set = parse_type_set(actual_val)

            actual_normalized = {normalize_for_display(t) for t in actual_set}
            verified_from_file = parse_verified_types(verified_str)

            if verified_from_file:
                # Use verified_types from file, but only count types that are in actual
                verified_normalized = verified_from_file & actual_normalized
            else:
                expected_normalized = {normalize_for_display(t) for t in expected_set}
                if expected_normalized == actual_normalized:
                    verified_normalized = expected_normalized
                else:
                    verified_normalized = expected_normalized & actual_normalized

            # Build expected_dict: expected from analysis
            for expected_type in expected_set:
                normalized = normalize_for_display(expected_type)
                if normalized not in expected_dict:
                    expected_dict[normalized] = set()
                expected_dict[normalized].add(pr_url)

            # Build actual_dict: what the tool detected
            for actual_type in actual_set:
                normalized = normalize_for_display(actual_type)
                if normalized not in actual_dict:
                    actual_dict[normalized] = set()
                actual_dict[normalized].add(pr_url)

            # Build verified_dict: verified correct detections
            for verified_type in verified_normalized:
                if verified_type not in verified_dict:
                    verified_dict[verified_type] = set()
                verified_dict[verified_type].add(pr_url)

            # Build not_verified_dict: detected but not verified (false positives)
            for actual_type in actual_set:
                normalized = normalize_for_display(actual_type)
                if normalized not in verified_normalized:
                    if normalized not in not_verified_dict:
                        not_verified_dict[normalized] = set()
                    not_verified_dict[normalized].add(pr_url)

    # Build the table
    total_unique_prs = len(analyzed_pr_urls) if analyzed_pr_urls else filtered_df['pr_url'].nunique()

    print("\n" + "="*80)
    print("Defect Detection Recall and Precision by Type on Historical Dataset")
    print("="*80)
    print()

    # Table data
    table_data = []
    total_expected = 0
    total_actual = 0
    total_verified = 0

    for type_code in ['A.1', 'A.2', 'B.1', 'B.2']:
        expected = len(expected_dict.get(type_code, set()))
        actual = len(actual_dict.get(type_code, set()))
        verified = len(verified_dict.get(type_code, set()))

        table_data.append({
            'type': type_names.get(type_code, type_code),
            'expected': expected,
            'actual': actual,
            'verified': verified
        })

        total_expected += expected
        total_actual += actual
        total_verified += verified

    # Print table
    print(f"Total Unique PRs in Dataset: {total_unique_prs}")
    print()
    print(f"{'Defect Type':<45} {'Expected':>10} {'Actual':>10} {'Verified':>10}")
    print("-" * 80)
    for row in table_data:
        print(f"{row['type']:<45} {row['expected']:>10} {row['actual']:>10} {row['verified']:>10}")
    print("-" * 80)
    print(f"{'Total':<45} {total_expected:>10} {total_actual:>10} {total_verified:>10}")
    print()

    # Print not verified (false positives) by type
    print("\n" + "="*80)
    print("Not Verified Detections (False Positives) by Type")
    print("="*80)
    print()

    for type_code in ['A.1', 'A.2', 'B.1', 'B.2']:
        not_verified = not_verified_dict.get(type_code, set())
        if not_verified:
            print(f"{type_names.get(type_code, type_code)} ({len(not_verified)} PRs):")
            for pr_url in sorted(not_verified):
                print(f"  - {pr_url}")
            print()
        else:
            print(f"{type_names.get(type_code, type_code)}: No false positives")
            print()
    print()

    # Generate LaTeX table
    latex_lines = []
    latex_lines.append("\\begin{table}[t]")
    latex_lines.append("\\centering")
    latex_lines.append("\\footnotesize")
    latex_lines.append(f"\\caption{{Defect Detection Performance on Historical Dataset ({total_unique_prs} unique PRs)}}")
    latex_lines.append("\\label{tab:defect_detection_performance}")
    latex_lines.append("\\begin{tabular}{lrrr}")
    latex_lines.append("\\toprule")
    latex_lines.append("\\textbf{Defect Type} & \\textbf{Expected} & \\textbf{Actual} & \\textbf{Verified} \\\\")
    latex_lines.append("\\midrule")

    for row in table_data:
        latex_lines.append(f"{row['type']} & {row['expected']} & {row['actual']} & {row['verified']} \\\\")

    latex_lines.append("\\midrule")
    latex_lines.append(f"\\textbf{{Total}} & \\textbf{{{total_expected}}} & \\textbf{{{total_actual}}} & \\textbf{{{total_verified}}} \\\\")
    latex_lines.append("\\bottomrule")
    latex_lines.append("\\end{tabular}")
    latex_lines.append("\\end{table}")

    latex_table = "\n".join(latex_lines)

    print("LaTeX Table:")
    print("="*80)
    print(latex_table)
    print("="*80)
    print()

    # Save to file
    output_dir = str(DATA_DIR)
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "defect_detection_recall.md")

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# Defect Detection Performance Analysis\n\n")
        f.write("## Summary\n\n")
        f.write(f"Total Unique PRs: {total_unique_prs}\n")
        f.write(f"Total Expected: {total_expected}\n")
        f.write(f"Total Actual: {total_actual}\n")
        f.write(f"Total Verified: {total_verified}\n\n")

        f.write("## By Type\n\n")
        f.write("| Defect Type | Expected | Actual | Verified |\n")
        f.write("|-------------|----------|---------|----------|\n")
        for row in table_data:
            f.write(f"| {row['type']} | {row['expected']} | {row['actual']} | {row['verified']} |\n")
        f.write(f"| **Total** | **{total_expected}** | **{total_actual}** | **{total_verified}** |\n\n")

        f.write("## LaTeX Table\n\n")
        f.write("```latex\n")
        f.write(latex_table)
        f.write("\n```\n")

    print(f"Results saved to: {output_file}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Run experiments on rosdistro PRs')
    parser.add_argument('command', nargs='?', default='run',
                        choices=['run', 'analyze', 'test'],
                        help='Command to run: run (analyze PRs), analyze (generate recall table), test (run tests)')
    args = parser.parse_args()

    if args.command == 'run':
        main()
    elif args.command == 'analyze':
        analyze_defect_detection_recall()
    elif args.command == 'test':
        test()

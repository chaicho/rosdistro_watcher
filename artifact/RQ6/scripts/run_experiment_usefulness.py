import os
import sys
import csv
import re
from pathlib import Path
from typing import Dict, List, Set, Optional
import logging
import traceback

# TOOL_PATH points to the main repo root for importing rosdep_auditor modules
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(TOOL_PATH))

from rosdep_auditor.config import load_config
from rosdep_auditor.problem_detector import (
    _collect_local_rosdep_yaml_contents,
    build_rosdep_database,
    check_entry_all_defects,
)
from rosdep_auditor.tools.logger import get_logger

logger = get_logger()


# =============================================================================
# HELPER FUNCTIONS - SIMPLIFIED REPORT GENERATION
# =============================================================================

def _format_defects_as_markdown(defects: dict, entry_key: str) -> str:
    """
    Convert defects dict returned by check_entry_all_defects into a readable
    Markdown section for a single entry.
    """
    lines = [f"\n### Entry: `{entry_key}`\n"]

    if not defects or all(len(v or []) == 0 for v in defects.values()):
        lines.append("- No issues detected\n")
        return "\n".join(lines)

    type_descriptions = {
        'A.1': 'Entry not found in ROSdep database',
        'A.2': 'Missing platform/version in entry',
        'B.1a': 'Package not found in repository',
        'B.1b': 'Package name differs from expected',
        'B.2': 'Suboptimal package manager usage',
    }

    for defect_type in sorted(defects.keys()):
        items = defects.get(defect_type, []) or []
        if not items:
            continue

        # Map B.2a to B.2 for unified display
        display_type = 'B.2' if defect_type == 'B.2a' else defect_type
        title = type_descriptions.get(display_type, display_type)
        lines.append(f"- **Type {display_type}**: {title}")

        for item in items:
            desc = item.get('description', '')
            lines.append(f"  - {desc}")
        lines.append("")

    return "\n".join(lines)


def _write_csv_row(csv_path: str, entry_key: str, defects: dict, error: Exception = None) -> None:
    """
    Write a single entry's defects to CSV (progressive streaming).

    Note: Error entries are NOT written to CSV, so they can be retried on resume.
    """
    headers = [
        'entry_key', 'type', 'entry',
        'missing_type', 'platform', 'version', 'package_name',
        'expected_package', 'detected_package', 'os_platform', 'os_version',
        'os_package', 'current_manager', 'suggested_platform',
        'suggested_version', 'suggested_package', 'description', 'status'
    ]

    # Don't write error entries - they should be retried on resume
    if error is not None:
        return

    file_exists = os.path.exists(csv_path)

    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()

        # Map B.2a to B.2 for unified output
        wrote_any = False
        for d_type, items in (defects or {}).items():
            write_type = 'B.2' if d_type == 'B.2a' else d_type
            for it in items or []:
                wrote_any = True
                writer.writerow({
                    'entry_key': entry_key,
                    'type': write_type,
                    'entry': it.get('entry', entry_key),
                    'missing_type': it.get('missing_type', ''),
                    'platform': it.get('platform', ''),
                    'version': it.get('version', ''),
                    'package_name': it.get('package_name', ''),
                    'expected_package': it.get('expected_package', ''),
                    'detected_package': it.get('detected_package', ''),
                    'os_platform': it.get('os_platform', ''),
                    'os_version': it.get('os_version', ''),
                    'os_package': it.get('os_package', ''),
                    'current_manager': it.get('current_manager', ''),
                    'suggested_platform': it.get('suggested_platform', ''),
                    'suggested_version': it.get('suggested_version', ''),
                    'suggested_package': it.get('suggested_package', ''),
                    'description': it.get('description', ''),
                    'status': 'ok',
                })

        if not wrote_any:
            writer.writerow({
                'entry_key': entry_key,
                'type': '',
                'entry': entry_key,
                'status': 'no_issues',
            })


def _init_reports(output_dir: str, output: str = '') -> tuple:
    """
    Initialize report files.
    Returns (md_path, csv_path).
    """
    os.makedirs(output_dir, exist_ok=True)

    md_path = os.path.join(output_dir, f'defects{output}.md')
    csv_path = os.path.join(output_dir, f'defects{output}.csv')

    # Initialize MD header
    if not os.path.exists(md_path):
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# Defects Report\n")
            f.write("---\n\n")

    return md_path, csv_path


def _load_detection_context(config_path: str = None) -> tuple:
    """
    Load the RQ6 configuration and build the rosdep database.
    """
    if config_path is None:
        config_path = str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_usefulness.yaml')
    config = load_config(config_path)
    rosdep_dir = str(TOOL_PATH / 'rosdep')

    # RQ6 targets the central rosdep index entries in base.yaml and python.yaml.
    file_content_dict = _collect_local_rosdep_yaml_contents(rosdep_dir, include_distribution=False)
    rosdep_database = build_rosdep_database(config, file_content_dict)
    return config, rosdep_database



# =============================================================================
# BLOCK 1: RUN DEFECT CHECK
# =============================================================================

def run_defect_check(
    entries_to_check: Optional[List[str]] = None,
    config_path: str = None,
    resume: bool = False,
    reverse: bool = False,
    start_idx: Optional[int] = None,
    end_idx: Optional[int] = None,
    output: str = '',
    dry_run: bool = False,
) -> Dict:
    """
    Run defect detection and generate reports.

    Args:
        entries_to_check: Optional list of specific entries to check
        config_path: Path to configuration YAML file
        resume: If True, skip entries already processed in existing CSV files
        reverse: If True, process entries in reverse order
        start_idx: Start index (1-based, inclusive). If None, starts from 1
        end_idx: End index (1-based, inclusive). If None, goes to the end
        output: Suffix for output files (e.g., '_partial')
        dry_run: If True, do NOT write to output files (CSV/MD).
                 Use with --start/--end to preview a subset.

    Returns:
        Dict with summary statistics
    """
    config, rosdep_database = _load_detection_context(config_path)

    # Initialize output directories and files
    data_dir = TOOL_PATH / 'artifact' / 'RQ6' / 'data'
    output_dir = str(data_dir)

    # Initialize report files (skip in dry-run mode — no output written)
    md_path, csv_path = ('', '')
    if not dry_run:
        md_path, csv_path = _init_reports(output_dir, output)

    # Build list of entries to check
    if entries_to_check:
        entries = entries_to_check
    else:
        entries = sorted(rosdep_database.get_all_entries(include_distribution_entries=False))

    # Resume mode: load already processed entries from CSV file
    processed_entries = set()
    if resume and not dry_run and os.path.exists(csv_path):
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                entry_key = row.get('entry_key', '')
                if entry_key:
                    processed_entries.add(entry_key)
        # Filter out already processed entries
        entries = [e for e in entries if e not in processed_entries]
        if processed_entries:
            print(f"[Resume] Skipping {len(processed_entries)} already processed entries")
            print(f"[Resume] Remaining entries to check: {len(entries)}")

    # Apply index range filtering (1-based indexing)
    if start_idx is not None or end_idx is not None:
        original_count = len(entries)
        # Convert to 0-based indexing
        start = (start_idx - 1) if start_idx is not None else 0
        end = end_idx if end_idx is not None else len(entries)
        # Ensure indices are valid
        start = max(0, min(start, len(entries) - 1))
        end = max(1, min(end, len(entries)))
        entries = entries[start:end]
        print(f"[Range] Processing entries {start + 1} to {end} (total: {len(entries)} entries, was {original_count})")

    # Reverse order if requested
    if reverse:
        entries = list(reversed(entries))
        print(f"[Reverse] Processing entries in reverse order (from {entries[0] if entries else 'empty'} to {entries[-1] if entries else 'empty'})")

    # Statistics
    stats = {
        'total_entries_in_list': len(entries),
        'total_checked': 0,
        'total_errors': 0,
        'total_defects': 0,
        'defects_by_type': {
            'A.1': 0, 'A.2': 0, 'B.1a': 0, 'B.1b': 0, 'B.2': 0
        }
    }

    # Process each entry
    for idx, entry_key in enumerate(entries, 1):
        try:
            # Check all defects for this entry
            defects = check_entry_all_defects(config, rosdep_database, entry_key)

            # Increment checked counter
            stats['total_checked'] += 1

            # Count defects
            entry_defect_count = sum(len(v or []) for v in defects.values())
            stats['total_defects'] += entry_defect_count

            # Update per-type counts (map B.2a to B.2)
            for defect_type, items in defects.items():
                if items:
                    count_type = 'B.2' if defect_type == 'B.2a' else defect_type
                    stats['defects_by_type'][count_type] += len(items)

            # Write to markdown (skip in dry-run mode)
            if not dry_run:
                md_content = _format_defects_as_markdown(defects, entry_key)
                with open(md_path, 'a', encoding='utf-8') as f:
                    f.write(md_content)

                # Write to CSV
                _write_csv_row(csv_path, entry_key, defects)

            print(f"[{idx}/{len(entries)}] Checked '{entry_key}': {entry_defect_count} defects")

        except Exception as e:
            stats['total_errors'] += 1
            logger.error(f"[{idx}/{len(entries)}] Error checking '{entry_key}': {e}", exc_info=True)

            if not dry_run:
                with open(md_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n### Entry: `{entry_key}`\n")
                    f.write(f"- Error: {str(e)}\n")

            # Don't write to CSV - error entries will be retried on resume

    print(f"\n{'='*60}")
    print(f"Defect check complete!")
    if dry_run:
        print(f"[Dry-Run] No output files were written.")
    print(f"Total entries in list: {stats['total_entries_in_list']}")
    print(f"Entries checked: {stats['total_checked']}")
    print(f"Entries with errors: {stats['total_errors']}")
    print(f"\nTotal defects: {stats['total_defects']}")
    print(f"\nDefects by type:")
    for defect_type, count in stats['defects_by_type'].items():
        if count > 0:
            print(f"  {defect_type}: {count}")
    print(f"{'='*60}\n")

    if not dry_run:
        print(f"Reports generated:")
        print(f"  {md_path}, {csv_path}")

    return stats


# =============================================================================
# BLOCK 2: ANALYZE DEFECT DISTRIBUTION
# =============================================================================

def analyze_defect_distribution(
    csv_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """
    Analyze defect distribution from CSV files and generate statistics.

    Args:
        csv_dir: Directory containing the defect CSV files
        output_dir: Directory to write the analysis report

    Returns:
        Path to the generated markdown report
    """
    if csv_dir is None:
        csv_dir = str(TOOL_PATH / 'artifact' / 'RQ6' / 'data')

    if output_dir is None:
        output_dir = csv_dir

    os.makedirs(output_dir, exist_ok=True)

    # Read CSV file
    csv_path = os.path.join(csv_dir, 'defects.csv')

    # Defect types with hierarchy
    primary_types = ['A.1', 'A.2', 'B.1', 'B.2']
    sub_types = ['B.1a', 'B.1b']

    # Mapping from sub-type to primary type
    # B.2a is mapped to B.2 (but we don't track B.2a as a sub-type for display)
    sub_to_primary = {
        'B.1a': 'B.1',
        'B.1b': 'B.1',
        'B.2a': 'B.2',
    }

    # Storage for analysis
    entry_to_types = {}      # For primary types
    entry_to_subtypes = {}   # For sub-types (B.1a, B.1b only)
    defect_records = []

    # Helper to read the CSV file
    if not os.path.exists(csv_path):
        print(f"Warning: CSV file not found: {csv_path}")
        return ""

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry_key = row.get('entry_key', '')
            defect_type = row.get('type', '')
            status = row.get('status', '')

            # Skip error and no_issues rows
            if status.startswith('error') or status == 'no_issues':
                continue

            if not entry_key or not defect_type:
                continue

            # Determine primary type and whether it's a sub-type
            primary_type = defect_type
            is_subtype = defect_type in sub_to_primary

            if is_subtype:
                primary_type = sub_to_primary[defect_type]
                # Track sub-type only for B.1a, B.1b (not B.2a)
                if defect_type in sub_types:
                    if entry_key not in entry_to_subtypes:
                        entry_to_subtypes[entry_key] = set()
                    entry_to_subtypes[entry_key].add(defect_type)

            # Track primary type (for A.1, A.2, B.1, B.2)
            if entry_key not in entry_to_types:
                entry_to_types[entry_key] = set()
            entry_to_types[entry_key].add(primary_type)

            # Track all defect instances
            defect_records.append((entry_key, defect_type))

    # Count primary defects by type (A.1, A.2, B.1, B.2)
    counts_by_primary = {t: sum(1 for types in entry_to_types.values() if t in types) for t in primary_types}

    # Count sub defects by type (B.1a, B.1b)
    counts_by_sub = {t: sum(1 for types in entry_to_subtypes.values() if t in types) for t in sub_types}

    # Calculate totals
    total_entries = len(entry_to_types)
    total_defects = len(defect_records)

    # Get total entries checked from CSV (unique entry_key values including those without defects)
    all_unique_entries = set()
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry_key = row.get('entry_key', '')
            if entry_key:
                all_unique_entries.add(entry_key)
    total_in_db = len(all_unique_entries)

    # Utility to render markdown tables
    def table_to_markdown(columns, rows):
        header = "| " + " | ".join(columns) + " |"
        sep = "| " + " | ".join(["---" for _ in columns]) + " |"
        lines = [header, sep]
        for r in rows:
            vals = [str(r.get(c, '')) for c in columns]
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    # Table 1: Primary defect types (A.1, A.2, B.1, B.2)
    primary_columns = ['Type', 'Count']
    primary_rows = []
    for t in primary_types:
        cnt = counts_by_primary.get(t, 0)
        primary_rows.append({
            'Type': t,
            'Count': cnt,
        })

    # Add total row for primary types
    primary_sum = sum(r['Count'] for r in primary_rows)
    primary_rows.append({
        'Type': '**Total**',
        'Count': primary_sum,
    })

    md_primary = table_to_markdown(primary_columns, primary_rows)

    # Table 2: Sub-type breakdown (B.1a, B.1b)
    sub_columns = ['Type', 'Count']
    sub_rows = []
    for t in sub_types:
        cnt = counts_by_sub.get(t, 0)
        sub_rows.append({
            'Type': t,
            'Count': cnt,
        })

    # Add total row for sub-types
    sub_sum = sum(r['Count'] for r in sub_rows)
    sub_rows.append({
        'Type': '**Total**',
        'Count': sub_sum,
    })

    md_sub = table_to_markdown(sub_columns, sub_rows)

    # Table 3: Entry perspective
    entry_columns = ['Metric', 'Count']
    entry_rows = [
        {
            'Metric': 'Total Entries in DB',
            'Count': total_in_db,
        },
        {
            'Metric': 'Entries With Defects',
            'Count': total_entries,
        }
    ]

    md_entries = table_to_markdown(entry_columns, entry_rows)

    # Final markdown
    markdown = f"# Defect Distribution Analysis\n\n"
    markdown += "---\n\n"

    # Primary categories table
    markdown += "## Primary Defect Categories (A.1, A.2, B.1, B.2)\n\n"
    markdown += md_primary + "\n\n"

    # Sub-type breakdown table
    markdown += "## Sub-Type Breakdown (B.1a, B.1b)\n\n"
    markdown += md_sub + "\n\n"

    # Explain the relationship: B.1 is a deduplicated union, so sub-type
    # counts sum to more than B.1 when entries have both B.1a and B.1b.
    overlap = counts_by_sub.get('B.1a', 0) + counts_by_sub.get('B.1b', 0) - counts_by_primary.get('B.1', 0)
    if overlap > 0:
        markdown += (
            f"_B.1 counts unique entries with at least one B.1 sub-type. "
            f"An entry can carry both B.1a and B.1b: {overlap} entries have both, "
            f"so the sub-type sum ({counts_by_sub.get('B.1a', 0)} + {counts_by_sub.get('B.1b', 0)} = "
            f"{counts_by_sub.get('B.1a', 0) + counts_by_sub.get('B.1b', 0)}) exceeds the deduplicated "
            f"B.1 count ({counts_by_primary.get('B.1', 0)})._\n\n"
        )

    # Entry perspective
    markdown += "## Entry Perspective\n\n"
    markdown += md_entries + "\n\n"

    # Type descriptions
    markdown += "## Defect Type Descriptions\n\n"
    type_desc = {
        'A.1': '**A.1**: Entry not found in ROSdep database',
        'A.2': '**A.2**: Missing platform/version in entry',
        'B.1': '**B.1**: Repository availability issues (aggregated from B.1a and B.1b)',
        'B.1a': '**B.1a**: Package not found in repository',
        'B.1b': '**B.1b**: Package name differs from expected',
        'B.2': '**B.2**: Suboptimal package manager usage',
    }
    for t, desc in type_desc.items():
        markdown += f"- {desc}\n"

    out_path = os.path.join(output_dir, 'defect_distribution_analysis.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(markdown)

    print("\n" + "="*60)
    print("PRIMARY DEFECT CATEGORIES (A.1, A.2, B.1, B.2)")
    print("="*60)
    print(md_primary)

    print("\n" + "="*60)
    print("SUB-TYPE BREAKDOWN (B.1a, B.1b)")
    print("="*60)
    print(md_sub)

    print("\n" + "="*60)
    print("ENTRY PERSPECTIVE")
    print("="*60)
    print(md_entries)
    print(f"\nMarkdown written to: {out_path}")

    return out_path


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """
    CLI interface:
      - check: Run defect detection
      - analyze: Analyze defect distribution
    """
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python run_experiment_usefulness.py check [--resume] [--reverse] [--start=N] [--end=M] [--output=SUFFIX] [--dry-run] [entries]")
        print("  python run_experiment_usefulness.py analyze")
        print("")
        print("Examples:")
        print("  # Dry-run first 50 entries (no file output)")
        print("  python run_experiment_usefulness.py check --dry-run --end=50")
        print("")
        print("  # Check entries 100 to 200")
        print("  python run_experiment_usefulness.py check --start=100 --end=200")
        print("")
        print("  # Check from entry 1000 to the end")
        print("  python run_experiment_usefulness.py check --start=1000")
        return

    command = sys.argv[1]

    if command == 'check':
        entries = None
        resume = False
        reverse = False
        start_idx = None
        end_idx = None
        output = ''
        dry_run = False

        for arg in sys.argv[2:]:
            if arg == '--resume':
                resume = True
            elif arg == '--reverse':
                reverse = True
            elif arg == '--dry-run':
                dry_run = True
            elif arg.startswith('--start='):
                start_idx = int(arg.split('=', 1)[1])
            elif arg.startswith('--end='):
                end_idx = int(arg.split('=', 1)[1])
            elif arg.startswith('--output='):
                output = arg.split('=', 1)[1]
            elif not arg.startswith('--'):
                # Treat as entries list
                entries = [e.strip() for e in arg.split(',') if e.strip()]

        stats = run_defect_check(
            entries_to_check=entries,
            resume=resume,
            reverse=reverse,
            start_idx=start_idx,
            end_idx=end_idx,
            output=output,
            dry_run=dry_run,
        )
        print("\nCheck complete.")

    elif command == 'analyze':
        analyze_defect_distribution()
        print("\nAnalyze complete.")

    else:
        print("Unknown command:", command)
        print("\nUsage:")
        print("  python run_experiment_usefulness.py check [--resume] [--reverse] [--start=N] [--end=M] [--output=SUFFIX] [--dry-run] [entries]")
        print("  python run_experiment_usefulness.py analyze")
        print("")
        print("Examples:")
        print("  # Dry-run first 50 entries (no file output)")
        print("  python run_experiment_usefulness.py check --dry-run --end=50")
        print("")
        print("  # Check entries 100 to 200")
        print("  python run_experiment_usefulness.py check --start=100 --end=200")
        print("")
        print("  # Check from entry 1000 to the end")
        print("  python run_experiment_usefulness.py check --start=1000")


if __name__ == "__main__":
    main()

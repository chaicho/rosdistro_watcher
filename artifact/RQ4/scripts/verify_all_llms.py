#!/usr/bin/env python3
"""
Verify all LLM results using ExistenceVerifier.
"""

import os
from pathlib import Path
import json
import sys
import re

# TOOL_PATH for accessing repo resources
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
# RQ4 data directory in artifact
DATA_DIR = TOOL_PATH / 'artifact' / 'RQ4' / 'data'


def verify_failed_packages(comparison_file_path: str):
    """
    Verify if LLM-recommended packages (marked as no match or partial match) actually exist.

    Args:
        comparison_file_path: Path to comparison markdown file

    Returns:
        Dictionary with verification results for each LLM
    """
    from rosdep_auditor.config import load_config
    from rosdep_auditor.existence_verifer import ExistenceVerifier

    # Setup
    config = load_config('rosdep_auditor/configs/config_distribution_comparison.yaml')
    supported_arches = config.get('supported_arches', {})
    verifier = ExistenceVerifier(config)

    # Read comparison file
    with open(comparison_file_path, 'r') as f:
        content = f.read()

    # Parse the markdown file
    lines = content.split('\n')

    results = {llm: {'total': 0, 'verified_exist': 0, 'verified_fake': 0} for llm in ['llm_glm_4.7', 'llm_gemini_3_pro', 'llm_claude_opus_4_5', 'llm_deepseek_v3.2']}

    current_package = None
    in_table = False
    llm_col_indices = {}  # Maps column name to index

    for i, line in enumerate(lines):
        # Track package name
        if line.startswith('## 📦 '):
            current_package = line.replace('## 📦 ', '').strip()
            in_table = False
            llm_col_indices = {}
            continue

        # Find table header
        if '| Platform |' in line and 'Ground Truth' in line:
            # Parse LLM column positions - use same filtering as data rows
            parts = [p.strip() for p in line.split('|') if p.strip()]
            for idx, part in enumerate(parts):
                if 'LLMDetector (glm-4.7)' in part:
                    llm_col_indices['llm_glm_4.7'] = idx
                elif 'LLMDetector (gemini-3-pro)' in part:
                    llm_col_indices['llm_gemini_3_pro'] = idx
                elif 'LLMDetector (claude-opus-4-5)' in part:
                    llm_col_indices['llm_claude_opus_4_5'] = idx
                elif 'LLMDetector (deepseek-v3.2)' in part:
                    llm_col_indices['llm_deepseek_v3.2'] = idx
            in_table = True
            continue

        if not in_table:
            continue

        # Skip separator lines and empty lines
        if line.startswith('|---') or not line.strip():
            continue

        # Parse data row
        if line.startswith('|'):
            # Split by | and strip whitespace, filter out empty strings
            parts = [p.strip() for p in line.split('|') if p.strip()]

            if len(parts) < 3:
                continue

            # Clean platform_key (remove markdown formatting)
            platform_key = parts[0].replace('**', '').strip()
            ground_truth = parts[1]

            # Skip header row
            if platform_key == 'Platform':
                continue

            # Parse platform and version
            if '_' in platform_key and platform_key.count('_') == 1:
                platform, version = platform_key.split('_', 1)
            else:
                platform = platform_key
                version = ""

            # Skip ros platform
            if platform == "ros":
                continue

            # Get architecture
            arch_list = supported_arches.get(platform, ['amd64'])
            arch = arch_list[0] if arch_list else 'amd64'

            # Check each LLM column
            for llm_key, col_idx in llm_col_indices.items():
                if col_idx >= len(parts):
                    continue

                llm_result = parts[col_idx]

                # Check for no match or partial match
                if '❌ No match' in llm_result or '⚠️ Partial match' in llm_result:
                    # Extract LLM bin_name (remove markdown formatting)
                    llm_bin_name = llm_result.split('<br/>')[0].strip().replace('`', '')

                    # Skip empty or already processed
                    if llm_bin_name in ['None', '', 'Empty'] or not llm_bin_name:
                        continue

                    results[llm_key]['total'] += 1

                    # Verify if the package exists
                    verified = verifier.verify(llm_bin_name, platform, version, arch)
                    if verified is not None:
                        results[llm_key]['verified_exist'] += 1
                    else:
                        results[llm_key]['verified_fake'] += 1

    return results


def main():
    from rosdep_auditor.config import load_config
    from rosdep_auditor.existence_verifer import ExistenceVerifier

    # Default LLMs to verify
    llms_to_verify = [
        "llm_deepseek_v3.2",
        "llm_claude_opus_4_5",
        "llm_gemini_3_pro",
        "llm_glm_4.7"
    ]

    if len(sys.argv) > 1:
        llms_to_verify = sys.argv[1:]

    print("Verifying LLM results for:")
    for llm in llms_to_verify:
        print(f"  - {llm}")

    # Setup
    config = load_config('rosdep_auditor/configs/config_distribution_comparison.yaml')
    supported_arches = config.get('supported_arches', {})
    verifier = ExistenceVerifier(config)
    result_dir = DATA_DIR / 'result'
    output_dir = DATA_DIR / 'verification_results'
    output_dir.mkdir(parents=True, exist_ok=True)

    all_summaries = {}

    for llm_name in llms_to_verify:
        print(f"\n{'='*60}")
        print(f"Verifying: {llm_name}")
        print(f"{'='*60}")

        llm_dir = result_dir / llm_name
        if not llm_dir.exists():
            print(f"Directory not found: {llm_dir}")
            continue

        # Load and parse all JSON files
        verified_count = 0
        unverified_count = 0
        fake_packages = []
        packages_seen = set()

        for json_file in sorted(llm_dir.glob('*.json')):
            package_name = json_file.stem
            with open(json_file, 'r') as f:
                data = json.load(f)

            # Parse each platform entry
            for platform_key, entries in data.items():
                # Parse platform and version from platform_key
                if '_' in platform_key and platform_key.count('_') == 1:
                    platform, version = platform_key.split('_', 1)
                else:
                    platform = platform_key
                    version = ""

                # Skip ros platform
                if platform == "ros":
                    continue

                if package_name not in packages_seen:
                    packages_seen.add(package_name)

                # Get architecture
                arch_list = supported_arches.get(platform, ['amd64'])
                arch = arch_list[0] if arch_list else 'amd64'

                # Verify each entry
                for entry in entries:
                    bin_name = entry.get("bin_name")
                    repo_name = entry.get("repo_name", platform)

                    verified = verifier.verify(bin_name, repo_name, version, arch)
                    if verified is not None:
                        verified_count += 1
                    else:
                        unverified_count += 1
                        print(f"✗ {package_name:40s} | {platform_key:20s} | {bin_name} [FAKE]")
                        fake_packages.append({
                            "package": package_name,
                            "platform": platform_key,
                            "bin_name": bin_name
                        })

        # Save results
        total_packages = len(packages_seen)
        total_entries = verified_count + unverified_count
        llm_summary = {
            "llm_name": llm_name,
            "total_packages": total_packages,
            "total_entries": total_entries,
            "verified_entries": verified_count,
            "unverified_entries": unverified_count,
            "fake_packages_count": len(fake_packages),
            "verification_rate": f"{verified_count / total_entries * 100:.1f}%" if total_entries > 0 else "N/A",
            "fake_rate": f"{unverified_count / total_entries * 100:.1f}%" if total_entries > 0 else "N/A",
        }

        all_summaries[llm_name] = llm_summary

        with open(output_dir / f"{llm_name}_fake_packages.json", 'w') as f:
            json.dump(fake_packages, f, indent=2)
        with open(output_dir / f"{llm_name}_summary.json", 'w') as f:
            json.dump(llm_summary, f, indent=2)

        print(f"\n--- {llm_name} Summary ---")
        print(f"  Total packages: {total_packages}")
        print(f"  Total entries: {total_entries}")
        print(f"  Verified: {verified_count} ({verified_count / total_entries * 100:.1f}%)")
        print(f"  Fake: {unverified_count} ({unverified_count / total_entries * 100:.1f}%)")

    # Save overall summary
    with open(output_dir / "overall_summary.json", 'w') as f:
        json.dump(all_summaries, f, indent=2)

    print(f"\n{'='*60}")
    print(f"{'LLM':<30} {'Total':<10} {'Verified':<12} {'Fake':<12} {'Rate':<10}")
    print(f"{'='*60}")
    for llm_name, s in all_summaries.items():
        if "error" in s:
            print(f"{llm_name:<30} ERROR: {s['error']}")
        else:
            print(f"{llm_name:<30} {s['total_entries']:<10} {s['verified_entries']:<12} {s['unverified_entries']:<12} {s['verification_rate']:<10}")
    print(f"{'='*60}")


def count_packages_from_json_dir(json_dir: Path) -> dict:
    """
    Count total packages and entries from a directory of JSON files.

    Args:
        json_dir: Path to directory containing JSON files

    Returns:
        Dictionary with total_packages (unique file count) and total_entries count
    """
    if not json_dir.exists():
        return {"total_packages": 0, "total_entries": 0}

    total_packages = 0
    total_entries = 0

    for json_file in json_dir.glob('*.json'):
        total_packages += 1
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)

            # Count entries (platform-key -> list of packages)
            if isinstance(data, dict):
                for platform_key, entries in data.items():
                    if isinstance(entries, list):
                        total_entries += len(entries)
        except Exception:
            pass

    return {"total_packages": total_packages, "total_entries": total_entries}


def count_packages_from_combined_dir(combined_dir: Path) -> dict:
    """
    Count packages from combined directory (contains LLM results and rosdep_ci).

    Args:
        combined_dir: Path to combined directory

    Returns:
        Dictionary with counts for each source
    """
    if not combined_dir.exists():
        return {}

    sources = {
        'detector_w07': {"total_packages": 0, "total_entries": 0},
        'llm_glm_4.7': {"total_packages": 0, "total_entries": 0},
        'llm_gemini_3_pro': {"total_packages": 0, "total_entries": 0},
        'llm_claude_opus_4_5': {"total_packages": 0, "total_entries": 0},
        'llm_deepseek_v3.2': {"total_packages": 0, "total_entries": 0},
        'mapper': {"total_packages": 0, "total_entries": 0},
        'rosdep_ci': {"total_packages": 0, "total_entries": 0},
    }

    for json_file in combined_dir.glob('*.json'):
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                continue

            # Count entries for each source
            for source_key in sources.keys():
                result_key = f"{source_key}_result"
                if result_key in data:
                    result_data = data[result_key]
                    if isinstance(result_data, dict):
                        for platform_key, entries in result_data.items():
                            if isinstance(entries, list):
                                sources[source_key]["total_entries"] += len(entries)
                                # Only count package once per file
                                if len(entries) > 0:
                                    sources[source_key]["total_packages"] += 1
                        # Adjust package count (divide by number of platforms with results)
                        platform_count = len([k for k, v in result_data.items() if isinstance(v, list) and len(v) > 0])
                        if platform_count > 0:
                            sources[source_key]["total_packages"] -= (len(result_data) - platform_count)

        except Exception:
            pass

    # Adjust package counts (each JSON file is one package)
    json_file_count = len(list(combined_dir.glob('*.json')))
    for source in sources.values():
        if source["total_entries"] > 0:
            source["total_packages"] = min(source["total_packages"], json_file_count)

    return sources


def compare_all_sources():
    """
    Compare package counts from all sources:
    - detector_w07 (considered verified)
    - mapper (considered verified)
    - rosdep_ci (considered verified)
    - LLM results (from combined)
    """
    result_dir = DATA_DIR / 'result'

    print(f"\n{'='*80}")
    print(f"Package Count Comparison Across All Sources")
    print(f"{'='*80}\n")

    # Count from detector_w07 (verified)
    detector_w07_counts = count_packages_from_json_dir(result_dir / 'detector_w07')

    # Count from mapper (verified)
    mapper_counts = count_packages_from_json_dir(result_dir / 'mapper')

    # Count from combined (LLM results and rosdep_ci)
    combined_counts = count_packages_from_combined_dir(result_dir / 'combined')
    rosdep_ci_counts = combined_counts.get('rosdep_ci', {"total_packages": 0, "total_entries": 0})

    # Total verified packages (detector_w07 + mapper + rosdep_ci)
    total_verified_packages = (
        detector_w07_counts['total_packages'] +
        mapper_counts['total_packages'] +
        rosdep_ci_counts['total_packages']
    )

    print(f"Verified Sources (Ground Truth):")
    print(f"  detector_w07:         {detector_w07_counts['total_packages']:>6} packages, {detector_w07_counts['total_entries']:>6} entries")
    print(f"  mapper:               {mapper_counts['total_packages']:>6} packages, {mapper_counts['total_entries']:>6} entries")
    print(f"  rosdep_ci:            {rosdep_ci_counts['total_packages']:>6} packages, {rosdep_ci_counts['total_entries']:>6} entries")
    print(f"  {'-'*76}")
    print(f"  Total Verified:       {total_verified_packages:>6} packages")

    print(f"\n{'-'*80}")
    print(f"LLM Results (from combined/):")
    for llm_name in ['llm_glm_4.7', 'llm_gemini_3_pro', 'llm_claude_opus_4_5', 'llm_deepseek_v3.2']:
        counts = combined_counts.get(llm_name, {"total_packages": 0, "total_entries": 0})
        print(f"  {llm_name:<20} {counts['total_packages']:>6} packages, {counts['total_entries']:>6} entries")

    # Summary table
    print(f"\n{'='*80}")
    print(f"Summary Table:")
    print(f"{'='*80}")
    print(f"{'Source':<25} {'Packages':>12} {'Entries':>12} {'Verified':>12}")
    print(f"{'-'*80}")

    all_sources = [
        ('detector_w07', detector_w07_counts, detector_w07_counts['total_packages']),
        ('mapper', mapper_counts, mapper_counts['total_packages']),
        ('rosdep_ci', rosdep_ci_counts, rosdep_ci_counts['total_packages']),
        ('llm_glm_4.7', combined_counts.get('llm_glm_4.7', {}), 0),
        ('llm_gemini_3_pro', combined_counts.get('llm_gemini_3_pro', {}), 0),
        ('llm_claude_opus_4_5', combined_counts.get('llm_claude_opus_4_5', {}), 0),
        ('llm_deepseek_v3.2', combined_counts.get('llm_deepseek_v3.2', {}), 0),
    ]

    for name, counts, verified in all_sources:
        pkg_count = counts.get('total_packages', 0) if isinstance(counts, dict) else 0
        entry_count = counts.get('total_entries', 0) if isinstance(counts, dict) else 0
        print(f"{name:<25} {pkg_count:>12} {entry_count:>12} {verified:>12}")

    print(f"{'='*80}")

    # Save comparison results
    output_dir = DATA_DIR / 'verification_results'
    output_dir.mkdir(parents=True, exist_ok=True)

    comparison_results = {
        'verified_sources': {
            'detector_w07': {**detector_w07_counts, 'verified': detector_w07_counts['total_packages']},
            'mapper': {**mapper_counts, 'verified': mapper_counts['total_packages']},
            'rosdep_ci': {**rosdep_ci_counts, 'verified': rosdep_ci_counts['total_packages']},
        },
        'total_verified_packages': total_verified_packages,
        'llm_results': {k: v for k, v in combined_counts.items() if k.startswith('llm_')},
    }

    with open(output_dir / 'comparison_summary.json', 'w') as f:
        json.dump(comparison_results, f, indent=2)

    print(f"\nResults saved to: {output_dir / 'comparison_summary.json'}\n")

    return comparison_results


def _tool_display_to_key(display_name: str) -> str | None:
    """Map a markdown tool display name (e.g. '🔍 PackageDistributionDetector (w=0.7)')
    to an internal tool key (e.g. 'detector_w07')."""
    cleaned = re.sub(r'^[^\w]+', '', display_name).strip()

    if 'PackageDistributionDetector' in cleaned:
        return 'detector_w07'
    if 'RepologyMapper' in cleaned:
        return 'mapper'
    if 'RosdepCIMapper' in cleaned:
        return 'rosdep_ci'
    if 'LLMDetector' in cleaned:
        m = re.search(r'\(([^)]+)\)', cleaned)
        if m:
            preset = m.group(1).strip().replace('-', '_')
            return f'llm_{preset}'
    return None


def _parse_match_data_from_markdown(markdown_path: Path) -> tuple[dict, int]:
    """Parse exact/partial match counts and total platforms from the Summary table.

    The Summary section looks like:
        # Summary
        **Entries**: 112 | **Platforms**: 854
        | Tool | Exact | Partial | None | Rate |
        |------|-------|---------|------|------|
        | 🔍 PackageDistributionDetector (w=0.7) | 812 | 1 | 41 | 95.1% |

    Returns (match_data, total_platforms) where match_data is like:
        {'detector_w07': {'exact': 812, 'partial': 1}, ...}
    """
    with open(markdown_path, 'r') as f:
        lines = f.readlines()

    match_data = {}
    total_platforms = 0
    in_summary = False
    in_table = False

    for line in lines:
        stripped = line.strip()
        if stripped == '# Summary':
            in_summary = True
            continue
        if not in_summary:
            continue
        # Extract total platforms: **Platforms**: 854
        m = re.search(r'\*\*Platforms\*\*:\s*(\d+)', stripped)
        if m:
            total_platforms = int(m.group(1))
        if stripped.startswith('| Tool |'):
            in_table = True
            continue
        if not in_table or not stripped.startswith('|') or stripped.startswith('|---'):
            continue
        # End of table: next section heading or blank line
        if stripped.startswith('##') or stripped == '':
            break

        parts = [p.strip() for p in stripped.split('|') if p.strip()]
        if len(parts) < 5:
            continue

        key = _tool_display_to_key(parts[0])
        if key:
            match_data[key] = {'exact': int(parts[1]), 'partial': int(parts[2])}

    return match_data, total_platforms


# LaTeX display names and ordering for the publication table
_LATEX_TOOL_NAMES = {
    'detector_w07': r'\textsc{RosdepAuditor} (Our)',
    'rosdep_ci': 'RosdepCI',
    'mapper': 'Repology',
    'llm_gemini_3_pro': 'LLM (Gemini-3-Pro)',
    'llm_glm_4.7': 'LLM (GLM-4.7)',
    'llm_claude_opus_4_5': 'LLM (Claude-Opus-4.5)',
    'llm_deepseek_v3.2': 'LLM (DeepSeek-V3.2)',
}

_LATEX_TOOL_ORDER = [
    'detector_w07',
    'rosdep_ci',
    'mapper',
    'llm_gemini_3_pro',
    'llm_glm_4.7',
    'llm_claude_opus_4_5',
    'llm_deepseek_v3.2',
]


def _generate_latex_table(extra_stats: dict, total_platforms: int) -> str:
    """Generate a publication-ready LaTeX table from the computed statistics."""
    rows = []
    for tool in _LATEX_TOOL_ORDER:
        if tool not in extra_stats:
            continue
        s = extra_stats[tool]
        display = _LATEX_TOOL_NAMES.get(tool, tool)
        exact = s['exact_matches']
        partial = s['partial_matches']
        exact_rate = exact / total_platforms * 100
        total_rate = (exact + partial) / total_platforms * 100
        extra = s['extra_total']
        unverified = s['unverified_count']
        unverified_rate = s['unverified_rate'].replace('%', r'\%')  # Escape % for LaTeX

        # Bold the best rates (RosdepAuditor)
        if tool == 'detector_w07':
            exact_rate_str = rf'\textbf{{{exact_rate:.1f}\%}}'
            total_rate_str = rf'\textbf{{{total_rate:.1f}\%}}'
            extra_str = rf'\textbf{{{extra:,} ({unverified}, {unverified_rate})}}'
        else:
            exact_rate_str = rf'{exact_rate:.1f}\%'
            total_rate_str = rf'{total_rate:.1f}\%'
            extra_str = f'{extra:,} ({unverified}, {unverified_rate})'

        rows.append(
            f'{display} & {exact} & {partial} &  {exact_rate_str} & {total_rate_str} & {extra_str} \\\\'
        )

    # RosdepAuditor first, then midrule, then baselines + LLMs
    our_row = rows[0]
    other_rows = '\n'.join(rows[1:])

    return rf"""\begin{{table*}}[t]
\centering
\scriptsize
\caption{{Performance Comparison of \textsc{{RosdepAuditor}}'s Equivalence Mapping against Baselines}}
\label{{tab:packagenexus_comparison}}
\begin{{tabular}}{{lccccc}}
\toprule
\textbf{{Approach}} & \textbf{{Exact Match}} & \textbf{{Partial Match}} & \textbf{{Exact Match Rate}} & \textbf{{Total Match Rate}} & \textbf{{Extra (Non-existent)}} \\
\midrule
{our_row}
\midrule
{other_rows}
\bottomrule
\end{{tabular}}
\end{{table*}}"""


def calculate_extra_nonexistent():
    """
    Calculate "Extra (Non-existent)" statistics for Table in RQ4.

    Extra (Non-existent) = (total_entries - exact_matches, unverified_count, unverified_rate)

    Returns:
        Dictionary with extra statistics for all tools
    """
    result_dir = DATA_DIR / 'result'
    rq4_dir = DATA_DIR / 'verification_results'

    # Load verified entries summary
    verified_summary_file = rq4_dir / 'verified_entries_summary.json'
    if not verified_summary_file.exists():
        print(f"Error: {verified_summary_file} not found. Run --summary-entries first.")
        return {}

    with open(verified_summary_file, 'r') as f:
        verified_data = json.load(f)

    # Parse exact/partial match counts from the comparison report's Summary table
    latest_report = DATA_DIR / 'final_version.md'
    if not latest_report.exists():
        print("Error: No comparison report found.")
        return {}

    match_data, total_platforms = _parse_match_data_from_markdown(latest_report)

    if not match_data:
        print("Error: Could not parse any match data from the report Summary table.")
        return {}

    if total_platforms == 0:
        print("Error: Could not parse total platforms from the report.")
        return {}

    extra_stats = {}

    for tool_name, match_counts in match_data.items():
        if tool_name not in verified_data:
            print(f"Warning: {tool_name} not in verified summary")
            continue

        entries = verified_data[tool_name]['entries']
        verified_entries = verified_data[tool_name]['verified_entries']

        exact_count = match_counts['exact']
        partial_count = match_counts['partial']
        total_matches = exact_count + partial_count

        # Calculate:
        # 1. total_entries - (exact_matches + partial_matches)
        # 2. entries - verified_entries (unverified/non-existent)
        # 3. (entries - verified_entries) / entries * 100%

        extra_total = entries - total_matches
        unverified_count = entries - verified_entries
        unverified_rate = (unverified_count / extra_total * 100) if entries > 0 else 0.0

        extra_stats[tool_name] = {
            'entries': entries,
            'exact_matches': exact_count,
            'partial_matches': partial_count,
            'total_matches': total_matches,
            'verified_entries': verified_entries,
            'extra_total': extra_total,
            'unverified_count': unverified_count,
            'unverified_rate': f"{unverified_rate:.1f}%"
        }

    # Print summary table
    print("=" * 100)
    print(f"{'Tool':<25} {'Entries':>10} {'Exact':>10} {'Verified':>10} {'Extra':>12} {'Unverified':>15} {'Rate':>10}")
    print("=" * 100)

    tool_order = ['detector_w07', 'mapper', 'rosdep_ci',
                  'llm_gemini_3_pro', 'llm_glm_4.7', 'llm_claude_opus_4_5', 'llm_deepseek_v3.2']

    for tool in tool_order:
        if tool in extra_stats:
            s = extra_stats[tool]
            print(f"{tool:<25} {s['entries']:>10} {s['exact_matches']:>10} {s['verified_entries']:>10} "
                  f"{s['extra_total']:>12} {s['unverified_count']:>15} {s['unverified_rate']:>10}")

    print("=" * 100)

    # Save to file
    output_file = rq4_dir / 'extra_nonexistent_stats.json'
    with open(output_file, 'w') as f:
        json.dump(extra_stats, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    # Print LaTeX table
    latex_table = _generate_latex_table(extra_stats, total_platforms)
    print("\n" + "=" * 100)
    print("LaTeX Table:")
    print("=" * 100)
    print(latex_table)
    print("=" * 100)

    return extra_stats


def summarize_verified_entries():
    """
    Summarize verified entries for all tools:
    - Non-LLM tools (detector_w07, rosdep_ci, mapper): all entries are verified
    - LLM tools: load verification results from *_summary.json files

    Returns:
        Dictionary with entry counts and verification rates for all tools
    """
    result_dir = DATA_DIR / 'result'
    rq4_dir = DATA_DIR / 'verification_results'
    rq4_dir.mkdir(parents=True, exist_ok=True)

    # Non-LLM tools: entries = verified packages (100% verification rate)
    non_llm_tools = {
        'detector_w07': result_dir / 'detector_w07',
        'rosdep_ci': result_dir / 'rosdep_ci',
        'mapper': result_dir / 'mapper',
    }

    # LLM tools - need verification data
    llm_tools = [
        'llm_gemini_3_pro',
        'llm_claude_opus_4_5',
        'llm_deepseek_v3.2',
        'llm_glm_4.7',
    ]

    summary = {}

    # Count entries for non-LLM tools (all entries are verified)
    for tool_name, tool_dir in non_llm_tools.items():
        if not tool_dir.exists():
            summary[tool_name] = {'entries': 0, 'verified_entries': 0, 'files': 0}
            continue

        total_entries = 0
        file_count = 0

        for json_file in sorted(tool_dir.glob('*.json')):
            file_count += 1
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    for platform_key, entries in data.items():
                        if isinstance(entries, list):
                            total_entries += len(entries)
            except Exception as e:
                print(f"Error reading {json_file}: {e}")

        summary[tool_name] = {
            'entries': total_entries,
            'verified_entries': total_entries,  # All entries are verified for non-LLM
            'files': file_count
        }

    # Load LLM verification results if available
    for tool_name in llm_tools:
        summary_file = rq4_dir / f"{tool_name}_summary.json"
        if summary_file.exists():
            with open(summary_file, 'r') as f:
                data = json.load(f)
            summary[tool_name] = {
                'entries': data.get('total_entries', 0),
                'verified_entries': data.get('verified_entries', 0),
                'files': data.get('total_packages', 0)
            }
        else:
            # If no verification data, count raw entries (not verified)
            tool_dir = result_dir / tool_name
            total_entries = 0
            file_count = 0
            for json_file in sorted(tool_dir.glob('*.json')):
                file_count += 1
                try:
                    with open(json_file, 'r') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        for platform_key, entries in data.items():
                            if isinstance(entries, list):
                                total_entries += len(entries)
                except Exception:
                    pass
            summary[tool_name] = {
                'entries': total_entries,
                'verified_entries': 0,  # Not verified
                'files': file_count
            }

    # Print summary
    print("=" * 80)
    print(f"{'Tool':<25} {'Entries':>12} {'Verified':>12} {'Verify Rate':>15} {'Files':>10}")
    print("=" * 80)

    # Non-LLM tools
    for tool in ['detector_w07', 'rosdep_ci', 'mapper']:
        if tool in summary:
            s = summary[tool]
            rate = "100%" if s['verified_entries'] == s['entries'] else "N/A"
            print(f"{tool:<25} {s['entries']:>12} {s['verified_entries']:>12} {rate:>15} {s['files']:>10}")

    print("-" * 80)

    # LLM tools
    for tool in llm_tools:
        if tool in summary:
            s = summary[tool]
            if s['verified_entries'] == 'N/A':
                rate = "N/A"
            elif isinstance(s['verified_entries'], str):
                rate = "N/A"
            elif s['entries'] > 0:
                rate = f"{s['verified_entries'] / s['entries'] * 100:.1f}%"
            else:
                rate = "N/A"
            print(f"{tool:<25} {s['entries']:>12} {s['verified_entries']:>12} {rate:>15} {s['files']:>10}")

    print("=" * 80)

    # Save summary
    output_path = rq4_dir / 'verified_entries_summary.json'
    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\nSummary saved to: {output_path}")

    return summary


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--failed':
        # Verify failed packages from comparison file
        comparison_file = sys.argv[2] if len(sys.argv) > 2 else str(DATA_DIR / 'comparison_20260127_003008.md')
        print(f"Verifying failed packages from: {comparison_file}")
        results = verify_failed_packages(comparison_file)

        # Print summary
        print(f"\n{'='*60}")
        print(f"Summary of LLM Recommended Packages (from no match/partial match)")
        print(f"{'='*60}")
        print(f"{'LLM':<30} {'Total':<10} {'Exist':<10} {'Fake':<10}")
        print(f"{'='*60}")
        for llm_name, stats in results.items():
            print(f"{llm_name:<30} {stats['total']:<10} {stats['verified_exist']:<10} {stats['verified_fake']:<10}")
        print(f"{'='*60}")

        # Save results
        output_dir = DATA_DIR / 'verification_results'
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / 'failed_packages_verification.json', 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {output_dir / 'failed_packages_verification.json'}")
    elif len(sys.argv) > 1 and sys.argv[1] == '--compare':
        # Compare all sources
        compare_all_sources()
    elif len(sys.argv) > 1 and sys.argv[1] == '--summary-entries':
        # Summarize verified entries for all tools
        summarize_verified_entries()
    elif len(sys.argv) > 1 and sys.argv[1] == '--extra-nonexistent':
        # Calculate Extra (Non-existent) statistics
        calculate_extra_nonexistent()
    else:
        main()

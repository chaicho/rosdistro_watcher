#!/usr/bin/env python3
"""
RQ4 Plot - Publication Quality Refactoring (Flatter Aspect Ratio)
Target: ACM/IEEE Transactions Standards
"""

import os
import re
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional
import numpy as np

# TOOL_PATH for accessing repo resources
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
# RQ4 data directory in artifact
DATA_DIR = TOOL_PATH / 'artifact' / 'RQ4' / 'data'

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# --- 1. Academic Style Configuration (Global) ---
config = {
    "font.family": "serif",
    "font.serif": ["Times New Roman"],
    "mathtext.fontset": "stix",
    
    # Font and line width adjustments
    "font.size": 14,
    "axes.labelsize": 16,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14,
    "legend.fontsize": 13,
    
    "axes.linewidth": 1.2,
    "lines.linewidth": 2.0,
    "lines.markersize": 7,
    "xtick.major.width": 1.2,
    "ytick.major.width": 1.2,

    # Adjust aspect ratio
    "figure.figsize": (8, 3.2), 
    "figure.constrained_layout.use": True, 
}
plt.rcParams.update(config)
# -----------------------------------------------

def parse_ranking_from_markdown(file_path: str) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Parse data from markdown (Logic preserved)."""
    exact_data = []
    total_data = []
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
        
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    summary_pattern = r'\|\s*🔍\s*PackageDistributionDetector\s*\(w=([\d.]+)\)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|'
    summary_matches = re.findall(summary_pattern, content)

    if summary_matches:
        total_platforms_match = re.search(r'\*\*Platforms\*\*:\s*(\d+)', content)
        total = int(total_platforms_match.group(1)) if total_platforms_match else None

        for weight_str, exact_str, partial_str, none_str in summary_matches:
            w = float(weight_str)
            exact = int(exact_str)
            partial = int(partial_str)

            if total and total > 0:
                exact_rate = (exact / total) * 100
                total_rate = ((exact + partial) / total) * 100
            else:
                total_count = exact + partial + int(none_str)
                exact_rate = (exact / total_count * 100) if total_count else 0
                total_rate = ((exact + partial) / total_count * 100) if total_count else 0

            exact_data.append((w, exact_rate))
            total_data.append((w, total_rate))
    else:
        # Fallback parsing
        ranking_match = re.search(r'## Ranking\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
        if ranking_match:
            ranking_section = ranking_match.group(1)
            patterns = [
                r'\*\*🔍\s*PackageDistributionDetector\s*\(w=([\d.]+)\)\*\*:\s*([\d.]+)%',
                r'🔍\s*PackageDistributionDetector\s*\(w=([\d.]+)\).*\|\s*([\d.]+)%\s*\|',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, ranking_section)
                if matches:
                    for weight_str, rate_str in matches:
                        exact_data.append((float(weight_str), float(rate_str)))
                        total_data.append((float(weight_str), float(rate_str)))

    # Deduplicate and sort
    seen = set()
    unique_exact = []
    unique_total = []
    combined = sorted([(e[0], e[1], t[1]) for e, t in zip(exact_data, total_data)], key=lambda x: x[0])
    
    for w, er, tr in combined:
        if w not in seen:
            seen.add(w)
            unique_exact.append((w, er))
            unique_total.append((w, tr))

    return unique_exact, unique_total


def plot_weight_vs_rate(
    exact_data: List[Tuple[float, float]],
    total_data: List[Tuple[float, float]],
    output_path: Optional[str] = None,
    show: bool = True
) -> None:
    """Generate a Tighter, Publication-Quality line chart."""
    if not exact_data or not total_data:
        print("Error: No data to plot.")
        return

    weights = [w for w, _ in exact_data]
    exact_rates = [r for _, r in exact_data]
    total_rates = [r for _, r in total_data]

    # Create Figure (flat aspect ratio)
    fig, ax = plt.subplots()

    # --- 1. Plotting Data ---
    ax.plot(weights, exact_rates, 
            marker='o', linestyle='-', color='#333333', 
            markerfacecolor='white', markeredgewidth=1.5,
            label='Exact Match Rate', zorder=3)

    ax.plot(weights, total_rates, 
            marker='s', linestyle='--', color='#005b96', 
            markerfacecolor='white', markeredgewidth=1.5,
            label='Total Match Rate', zorder=2)

    # --- 2. Smart Peak Annotation (Compacted) ---
    max_exact = max(exact_rates)
    peak_indices = [i for i, x in enumerate(exact_rates) if abs(x - max_exact) < 0.001]
    
    # Dynamically calculate Y axis range for compact display
    y_min_val = min(min(exact_rates), min(total_rates))
    y_max_val = max(max(exact_rates), max(total_rates))
    
    # Adjust margin for compact display
    # Bottom: 1.5, Top: 3.5
    y_bottom_limit = y_min_val - 1.5
    y_top_limit = y_max_val + 3.8 
    
    ax.set_ylim(y_bottom_limit, y_top_limit)

    if peak_indices:
        peak_ws = [weights[i] for i in peak_indices]
        center_w = sum(peak_ws) / len(peak_ws)

        # Get corresponding total rates at peak weights
        peak_totals = [total_rates[i] for i in peak_indices]
        max_total = max(peak_totals)

        if len(peak_ws) == 1:
            w_subtext = f"(w={peak_ws[0]})"
        elif len(peak_ws) == 2:
            w_subtext = f"(w={peak_ws[0]}, {peak_ws[1]})"
        else:
            w_subtext = f"(w={min(peak_ws)}-{max(peak_ws)})"

        label_text = f"Peak: {max_exact:.1f}% / {max_total:.1f}%\n{w_subtext}"

        # Adjust arrow length and text position for compact top space
        ax.annotate(label_text,
                    xy=(center_w, max_exact),
                    xytext=(center_w, max_exact + 2.5),
                    ha='center', va='bottom',
                    fontsize=13,
                    arrowprops=dict(arrowstyle='->', lw=1.2, color='black', shrinkA=0, shrinkB=3))

        if len(peak_indices) > 1:
             ax.scatter([weights[i] for i in peak_indices], 
                        [exact_rates[i] for i in peak_indices],
                        s=100, facecolors='none', edgecolors='#333333', linewidth=2, zorder=4)

    # --- 3. Refinements ---
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xlabel(r'Weight Parameter ($w$)', labelpad=6)
    ax.set_ylabel(r'Match Rate (%)', labelpad=6)
    ax.grid(axis='y', linestyle='--', alpha=0.3, color='gray', zorder=0)
    
    ax.set_xlim(0, 1.05)
    ax.xaxis.set_major_locator(ticker.MultipleLocator(0.1))
    
    # Legend
    ax.legend(frameon=False, loc='lower right', bbox_to_anchor=(1.0, 0.02))

    if output_path:
        plt.savefig(output_path, format='png', dpi=300)
        pdf_path = output_path.replace('.png', '.pdf')
        plt.savefig(pdf_path, format='pdf')
        print(f"Plots saved:\n  [PNG] {output_path}\n  [PDF] {pdf_path}")

    if show:
        plt.show()
    
    plt.close()
def main():
    parser = argparse.ArgumentParser(description="RQ4 Plot - Academic Style")
    parser.add_argument('-i', '--input', help='Path to comparison markdown file')
    parser.add_argument('-o', '--output', help='Path to save plot')
    parser.add_argument('--no-show', action='store_true', help='Do not display plot')

    args = parser.parse_args()

    # File resolution logic
    if args.input:
        input_file = args.input
    else:
        default_path = str(DATA_DIR / 'w_related_ver.md')
        if os.path.exists(default_path):
            input_file = default_path
        else:
            input_file = str(DATA_DIR / 'w_related_ver.md')
            
    if not os.path.exists(input_file):
        print(f"Error: Comparison file not found at {input_file}")
        return 1

    try:
        exact_data, total_data = parse_ranking_from_markdown(input_file)
        if not exact_data:
            print("Error: Parsed data is empty.")
            return 1
    except Exception as e:
        print(f"Error parsing file: {e}")
        return 1

    output_path = args.output
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = os.path.dirname(input_file)
        rq4_dir = os.path.join(os.path.dirname(base_dir), 'RQ4')
        if not os.path.exists(rq4_dir):
            rq4_dir = os.getcwd()
        output_path = os.path.join(rq4_dir, f"RQ4_Publication_{timestamp}.png")

    plot_weight_vs_rate(exact_data, total_data, output_path=output_path, show=not args.no_show)
    return 0

if __name__ == "__main__":
    exit(main())
import pandas as pd
from datetime import datetime
from pathlib import Path
import os

TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))


def filter_by_date(df, cutoff_date):
    """Filter DataFrame by created_at date if cutoff_date is provided."""
    if cutoff_date is None:
        return df
    if 'created_at' not in df.columns:
        print("Warning: 'created_at' column not found, skipping date filtering")
        return df
    df = df.copy()
    df['created_at'] = pd.to_datetime(df['created_at'], utc=True)
    df = df[df['created_at'] <= cutoff_date]
    print(f"Filtered to {len(df)} rows created before or on {cutoff_date}")
    return df


def load_csv(csv_file_path):
    """Load CSV file and return DataFrame."""
    try:
        df = pd.read_csv(csv_file_path)
        if df.empty:
            raise ValueError(f"CSV file {csv_file_path} is empty")
        return df
    except FileNotFoundError:
        raise FileNotFoundError(f"File {csv_file_path} not found")


def parse_classification_types(value):
    """Parse classification types from string to list."""
    if not isinstance(value, str) or value in ('No classification found', 'ERROR', 'Other'):
        return None
    return [t.strip() for t in value.split(',')]


def calculate_classification_prs(csv_file_path, cutoff_date=None):
    """Extract classification types for merged PRs."""
    df = load_csv(csv_file_path)
    df = filter_by_date(df, cutoff_date)
    
    # Filter merged PRs only
    df = df[df['status'] == 'merged']
    
    pr_classification_dict = {}
    for _, row in df.iterrows():
        classification_types = parse_classification_types(row['classification_types'])
        if classification_types:
            pr_classification_dict[row['pr_url']] = classification_types
    
    return pr_classification_dict


def calculate_modification_all(cutoff_date=None):
    """Extract modification types for merged PRs."""
    csv_file_path = str(TOOL_PATH / 'artifact' / 'RQ1' / 'data' / 'pr_manual_analyzed_final.csv')
    df = load_csv(csv_file_path)
    df = filter_by_date(df, cutoff_date)
    
    # Filter merged PRs only
    df = df[df['status'] == 'merged']
    
    pr_modification_dict = {}
    for _, row in df.iterrows():
        modification_types = row['chained_modifications_types']
        if isinstance(modification_types, str) and modification_types not in ('No modifications found', 'ERROR'):
            pr_modification_dict[row['pr_url']] = [t.strip() for t in modification_types.split(',')]
    
    return pr_modification_dict


def calculate_classification_issues(pr_classification_dict, cutoff_date=None):
    """Extract classification types for issues and track overlaps with PRs."""
    csv_file_path = str(TOOL_PATH / 'artifact' / 'RQ1' / 'data' / 'issues_analyzed.csv')
    df = load_csv(csv_file_path)
    
    if cutoff_date is not None:
        print("Warning: issues_analyzed.csv doesn't contain created_at field, skipping date filtering")
    
    issue_classification_dict = {}
    unique_issues_classification_dict = {}
    overlapping_pr_urls = set()  # Track PRs that are linked from issues
    plans_only_count = 0
    solutions_cnt = 0
    overlapping_solutions_cnt = 0
    
    for _, row in df.iterrows():
        html_url = row['html_url']
        classification_types = parse_classification_types(row['classification_types'])
        
        if not classification_types:
            continue
        
        issue_classification_dict[html_url] = classification_types
        unique_issues_classification_dict[html_url] = classification_types
        
        # Check if issue has a related PR
        related_pr = row['related_pr']
        if pd.notna(related_pr):
            solutions_cnt += 1
            pr_number = related_pr.split('#')[-1]
            concated_pr_url = f'https://github.com/ros/rosdistro/pull/{pr_number}'
            
            if concated_pr_url in pr_classification_dict:
                pr_classification_result = pr_classification_dict[concated_pr_url]
                print(f"Issue {html_url} is related to PR {concated_pr_url}")
                
                # Remove from unique since it's covered by PR
                unique_issues_classification_dict.pop(html_url, None)
                overlapping_pr_urls.add(concated_pr_url)
                
                # Verify classification consistency
                if classification_types[0] not in pr_classification_result:
                    print(f"  Warning: Issue type {classification_types} != PR type {pr_classification_result}")
                
                overlapping_solutions_cnt += 1
            else:
                print(f"Related PR {concated_pr_url} not found in pr_classification_dict")
        
        # Count plans-only issues
        if row['plans_only'] == True:
            plans_only_count += 1
    
    return (issue_classification_dict, plans_only_count, solutions_cnt, 
            overlapping_solutions_cnt, unique_issues_classification_dict, overlapping_pr_urls)


def count_types(classification_dict):
    """Count occurrences of each classification type."""
    type_cnt = {}
    for classification_types in classification_dict.values():
        for t in classification_types:
            t = t.strip()
            type_cnt[t] = type_cnt.get(t, 0) + 1
    return type_cnt


if __name__ == "__main__":
    cutoff_date = None  # Set to pd.Timestamp('2024-06-01', tz='UTC') for date filtering
    
    # Load PR classifications
    final_pr_classification = calculate_classification_prs(str(TOOL_PATH / 'artifact' / 'RQ1' / 'data' / 'pr_manual_analyzed_final.csv'), cutoff_date)
    modification_dict = calculate_modification_all(cutoff_date)
    
    # Load issue classifications (use final_pr for overlap detection)
    (issue_classification, plans_only_count, solutions_cnt, 
     overlapping_cnt, unique_issues, overlapping_pr_urls) = calculate_classification_issues(final_pr_classification, cutoff_date)
    
    # PRs only = final PRs - PRs that are linked from issues
    prs_only_count = len(final_pr_classification) - len(overlapping_pr_urls)
    
    # Combine unique issues with final PR classifications
    combined_classification = {**unique_issues, **final_pr_classification}
    
    # Print summary
    print("\n=== Summary ===")
    print(f"Total DM issues count: {len(combined_classification)}")
    print(f"  From issues (unique): {len(unique_issues)}")
    print(f"  From PRs (all, including overlapping): {len(final_pr_classification)}")
    print(f"    - PRs only: {prs_only_count}")
    print(f"    - Overlapping with issues: {len(overlapping_pr_urls)}")
    print(f"\n  Issues breakdown ({len(issue_classification)} total):")
    print(f"    - Plans only: {plans_only_count}")
    print(f"    - With solutions (PRs): {solutions_cnt}")
    print(f"    - Overlapping detected: {overlapping_cnt}")
    
    # Classification type counts
    type_counts = count_types(combined_classification)
    print(f"\n=== Classification Types ({len(combined_classification)} total) ===")
    for classification_type, cnt in sorted(type_counts.items()):
        print(f"  {classification_type}: {cnt}")

    # Print LaTeX table
    print("\n=== LaTeX Table ===")
    total = len(combined_classification)

    latex_table = r"""\begin{table}[t]
\caption{Taxonomy and Frequency of Central Index Defects}
\label{tab:defect-types}
\centering
\resizebox{\columnwidth}{!}{
\begin{tabular}{llc}
\toprule
Category & Defect Type & Frequency \\
\midrule
\textbf{A. Resolution Coverage Defects} & A.1 Missing Dependency Definition & """ + f"{type_counts.get('A.1', 0)} ({type_counts.get('A.1', 0)/total*100:.1f}\\%)" + r""" \\
(No Rule Found) & A.2 Incomplete Platform Coverage & """ + f"{type_counts.get('A.2', 0)} ({type_counts.get('A.2', 0)/total*100:.1f}\\%)" + r""" \\
\hline
\textbf{B. Resolution Correctness Defects} & B.1 Invalid Package Specification & """ + f"{type_counts.get('B.1', 0)} ({type_counts.get('B.1', 0)/total*100:.1f}\\%)" + r""" \\
(Incorrect Rule Found) & B.2 Suboptimal Repository Prioritization & """ + f"{type_counts.get('B.2', 0)} ({type_counts.get('B.2', 0)/total*100:.1f}\\%)" + r""" \\
\bottomrule
\end{tabular}
}
\end{table}"""

    print(latex_table)

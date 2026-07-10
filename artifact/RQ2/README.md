# RQ2: Maintenance Bottleneck

Analyzes maintenance effort (turnaround time, review comments, modifications) for rosdistro PRs.

## Files

### Scripts
- `RQ2.py` - Main analysis script for review latency, comments, post-review iteration, repeated-entry changes, and package-name inconsistency

### Usage
```bash
cd artifact/RQ2/scripts
python RQ2.py
```

`RQ2.py` reads the 835 classified merged PRs from RQ1 plus the packaged RQ2
CSV intermediates. It prints several analysis blocks:

- Merge latency by defect type and overall average/median resolution time.
- Commits after review request, including the number and percentage of PRs with
  post-review commits.
- Repeated rosdep-key modification statistics from `pr_modified_keys.csv`.
- Package-name inconsistency statistics from `key_packages.csv`, comparing raw
  package names and normalized package names.
- Review-comment totals from `comments_in_prs.csv`.
- Rule-related versus unrelated post-review modifications from
  `pr_chained_modification.csv`.

Expected paper-facing values from the packaged data include:

| Metric | Expected value |
| --- | ---: |
| PRs analyzed | 835 |
| Average resolution time | 248.6 hours |
| Review comments | 3,915 |
| Average comments per PR | 4.69 |
| PRs with manual review | 808 / 835 (96.8%) |
| PRs with post-review iteration | 555 / 835 (66.5%) |
| Rule-related post-review iterations | 402 / 555 (72.4%) |
| Unique modified entries | 1,921 |
| Entries modified by multiple PRs | 990 / 1,921 (51.5%) |
| Average updates per repeated entry | 2.94 |
| Repeated entries modified again within 30 days | 137 / 990 (13.8%) |
| Raw package-name-difference PRs with rule-related follow-up | 302 / 456 (66.2%) |
| No-raw-difference PRs with rule-related follow-up | 100 / 379 (26.4%) |

When the packaged CSVs already exist, the command analyzes them directly. If a
CSV is missing or empty, parts of the script may try to recollect data from
GitHub and require `GITHUB_TOKEN`; run that mode only in a disposable container
because it can rewrite the CSV intermediates.

### Data
- `artifact/RQ1/data/pr_manual_analyzed_final.csv` - The 835 classified PR cases reused from RQ1 as the main PR population for RQ2
- `comments_in_prs.csv` - Per-PR review/discussion comment counts used to compute total comments and average comments per PR
- `commits_after_review.csv` - Per-PR counts of commits added after review comments, used to measure post-review iteration
- `pr_modified_keys.csv` - Rosdep keys touched by each PR, used for repeated-entry and package-name-difference analyses
- `pr_chained_modification.csv` - Manually categorized within-PR follow-up modification types used to identify rule-related iterations
- `key_packages.csv` - Serialized before/after package mappings for the modified keys in each PR, used to analyze repeated entry updates across PRs

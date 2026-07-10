# RQ1: Taxonomy of Defects

Analyzes PRs and issues to identify defect types in the rosdep central index.

## Data Pipeline

```
GitHub Search API                        Manual Annotation                  Empirical Analysis
─────────────────                       ─────────────────                  ───────────────────
raw_prs.csv (1002)    ──→  pr_manual_analyzed_final.csv (835)  ──→  empirical_analysis.py  ──→  863 cases
raw_issues.csv (534)  ──→  issues_analyzed.csv (54)            ──→                          A.1=625, A.2=181,
                                                                                             B.1=111, B.2=12
```

The raw data (1002 PRs with `label:rosdep` and `comments >= 1`, 534 issues)
were mined from `ros/rosdistro` via the GitHub Search API
(`created:<=2025-04-30`). After manual annotation and deduplication
(overlapping issues removed), 835 PRs and 28 unique issues form the 863 cases.

## Files

### Scripts
- `empirical_analysis.py` — Generates defect type statistics from the annotated CSVs
- `mine_raw_data.py` — (Optional) Re-collects raw PRs and issues from GitHub Search API. Requires `GITHUB_TOKEN`. Outputs `raw_prs.csv` and `raw_issues.csv`

### Usage
```bash
# Main analysis (offline, uses packaged annotated data)
cd artifact/RQ1/scripts
python empirical_analysis.py

# Optional: re-collect raw data from GitHub (requires GITHUB_TOKEN)
python mine_raw_data.py
```

`empirical_analysis.py` reads the packaged manually annotated CSVs and prints
the paper-facing defect taxonomy counts. It does not call GitHub or rewrite the
input CSVs. Successful output includes:

- 863 total maintenance cases: 835 merged PR cases plus 28 non-overlapping
  issue cases.
- Defect counts A.1=625, A.2=181, B.1=111, B.2=12.
- The derived coverage/correctness totals used by the paper:
  A.1+A.2=806 and B.1+B.2=123.

`mine_raw_data.py` is only for optional recollection. It queries the GitHub
Search API for the raw candidate set used before manual annotation and writes
`raw_prs.csv` and `raw_issues.csv` under `artifact/RQ1/data/`. Recollected
counts may differ if GitHub repository metadata has changed since the artifact
snapshot.

### Data
- `raw_prs.csv` — 1002 raw PRs collected from GitHub (created ≤2025-04-30, label:rosdep, comments ≥1)
- `raw_issues.csv` — 534 raw issues collected from GitHub (created ≤2025-04-30)
- `pr_manual_analyzed_final.csv` — Manually analyzed PRs with defect classifications
- `issues_analyzed.csv` — Analyzed issues with classifications

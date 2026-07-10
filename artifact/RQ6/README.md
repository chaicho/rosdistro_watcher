# RQ6: Usefulness

Demonstrates practical usefulness through full index scan and developer validation.

## Files

### Scripts
- `run_experiment_usefulness.py` - Runs full index scan and generates defect reports
- `build_rq6_prospective_database.py` - Builds the prospective-validation PR database skeleton
- `generate_pr_defect_report.py` - Generates or analyzes the prospective-validation PR defect report

### Usage

#### Reproduce paper-facing result
```bash
python artifact/RQ6/scripts/run_experiment_usefulness.py analyze
python artifact/RQ6/scripts/generate_pr_defect_report.py --analyze artifact/RQ6/data/pr_defect_types_mapping_manual.csv
```

The first command regenerates the full-index defect distribution summary from
`defects.csv`. The second command regenerates the prospective-validation
summary from the manually completed PR-defect mapping file.

#### End-to-end workflow

RQ6 has two separate analysis flows.

1. Run or summarize the full-index scan:
```bash
cd artifact/RQ6/scripts

# Dry-run preview on a subset (no files written)
python run_experiment_usefulness.py check --dry-run --end=50

# Analyze defect distribution from packaged data (offline)
python run_experiment_usefulness.py analyze
```

The full-index scan checks the frozen `rosdep/` snapshot and writes
`defects.csv`; `analyze` summarizes that file into
`defect_distribution_analysis.md`. This flow does not use PR data. A full
`check` without `--dry-run` scans every entry against live indexes and can take
hours.

2. Build the prospective-validation PR skeleton:
```bash
python artifact/RQ6/scripts/build_rq6_prospective_database.py
```

This rebuilds `rq6_prospective_database.csv` from merged `rosdep` PRs created
from September 2025 through January 2026, extracts `modified_keys`, filters to
PRs whose keys exist in the frozen rosdep snapshot, and writes the blank
`pr_defect_types_mapping.csv` manual template.

3. Fill the prospective-validation mapping:

Manually fill `defect_types_addressed_in_pr`,
`defect_types_detect_from_keys`, `use_our_packages`, and `description` in the
blank mapping template. Save the completed version as
`artifact/RQ6/data/pr_defect_types_mapping_manual.csv`.

4. Generate the prospective-validation summary:
```bash
python artifact/RQ6/scripts/generate_pr_defect_report.py --analyze artifact/RQ6/data/pr_defect_types_mapping_manual.csv
```

This prints the paper-facing prospective-validation counts: 33 prospective PRs,
35 addressed defect instances, 32 exact identifications, and 21 same-package
PRs.

#### Script reference
```bash
cd artifact/RQ6/scripts

# Run or summarize full-index detection
python run_experiment_usefulness.py check --dry-run --end=50
python run_experiment_usefulness.py analyze

# Build prospective-validation skeleton files
python build_rq6_prospective_database.py

# Analyze the manually completed prospective mapping
python generate_pr_defect_report.py --analyze ../data/pr_defect_types_mapping_manual.csv
```

### Data
- `defects.csv` - Packaged full-index scan results used to report the paper's defect totals and subtype counts
- `defect_distribution_analysis.md` - The packaged summary of `defects.csv`, including the A.1/A.2/B.1/B.2 totals reported in the paper
- `submitted_issues.csv` - The 46 author-reported defect submissions used for the developer-feedback summary
- `rq6_prospective_database.csv` - The 33-PR prospective-validation dataset monitored after the main scan
- `pr_defect_types_mapping.csv` - Blank PR-defect mapping template generated from the prospective PR dataset for manual completion
- `pr_defect_types_mapping_manual.csv` - Manually completed mapping between prospective PRs and RosdepAuditor-detected defect types, used to compute the 33 PR / 35 instance / 32 overlap / 21 same-package results

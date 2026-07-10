# RosdepAuditor

RosdepAuditor automatically audits ROS's central dependency index. It infers
equivalent packages across heterogeneous repositories and diagnoses missing,
invalid, or suboptimally prioritized installation rules.

This repository contains both the tool and the ISSTA 2026 evaluation artifact.
The artifact evaluation guide (Getting Started + step-by-step reproduction)
starts below in Part I. Tool API examples for reuse live in
[`rosdep_auditor/README.md`](rosdep_auditor/README.md).

## Repository structure

```text
rosdep_auditor/       Core library, repository parsers, and defect detectors
                      — a reusable tool applicable to any rosdep index for
                      automated defect detection and equivalence mapping,
                      independent of the paper experiments.
artifact/             ISSTA 2026 data, scripts, and results
  RQ1/ ... RQ6/       Materials organized by research question, with per-RQ
                      README files detailing inputs, outputs, and
                      regeneration commands.
rosdep/               Frozen rosdep YAML snapshot used by the artifact
docker/               Container build, publication, and transfer helpers
Dockerfile            Reproducible artifact environment
```

| Area | Contents |
| --- | --- |
| `rosdep_auditor/` | Reusable equivalence-mapping and defect-diagnosis tool |
| `artifact/RQ1/` | Defect taxonomy |
| `artifact/RQ2/` | Maintenance bottleneck analysis |
| `artifact/RQ3/` | Cross-repository automation barriers |
| `artifact/RQ4/` | Equivalence-mapping evaluation and baselines |
| `artifact/RQ5/` | Historical defect-diagnosis evaluation |
| `artifact/RQ6/` | Full-index scan and prospective validation |

The artifact is carefully documented and well-structured to facilitate reuse
and repurposing. A self-contained Docker environment provides the full runtime
with no host-level dependencies beyond Docker.

---

# ISSTA 2026 Artifact Evaluation Guide

This is the main artifact README accompanying **"Guarding the Lifeline: A
First Look and Automated Defect Diagnosis for ROS Central Index."** It
contains the two parts required by the ISSTA 2026 Artifact Evaluation call: a
Getting Started guide and step-by-step reproduction instructions.

The artifact contains the RosdepAuditor implementation, the datasets and
scripts used for RQ1–RQ6, and the raw and summarized results reported in the
paper.

## Part I — Getting Started

### 1. Artifact description and contents

**In this archive:**

| Path | Purpose |
| --- | --- |
| `README.md` | This AE guide |
| `REQUIREMENTS.md` | Architecture, hardware, software, storage, and credential requirements |
| `STATUS.md` | Badges applied for and justification |
| `LICENSE.md` | MIT License |
| `rosdepauditor-artifact-image.tar.gz` | Pre-built Docker image (load with `docker load`) |
| `rosdep_auditor/` | Reusable RosdepAuditor implementation and tool examples |
| `rosdep/` | Frozen rosdep YAML snapshot (commit `667b7b5`) |
| `artifact/RQ1/`–`artifact/RQ6/` | Inputs, scripts, raw results, and summaries for each RQ |
| `artifact/paper_expected_results.json` | Values transcribed from the paper PDF |
| `artifact/run_quickstart.py` | Environment checker and artifact evaluation entry point |
| `docker/` | Container build, publication, and transfer helpers |
| `Dockerfile` | Reproducible reviewer environment |

To satisfy the ISSTA Artifact Evaluation expectation that the artifact be
self-contained and that its verification path work offline, the artifact
freezes the repository snapshots and cached repository metadata used by the
evaluation as of **June 2026**. This keeps the artifact results deterministic
and reviewable because the underlying `rosdep`, package-repository, and GitHub
repositories continue to evolve over time.

**Inside the loaded container** (paths under `/workspace`):

| Path | Purpose |
| --- | --- |
| `rosdep_auditor/` | RosdepAuditor implementation and tool API examples |
| `rosdep/` | Frozen rosdep YAML snapshot |
| `artifact/RQ1/`–`artifact/RQ6/` | Inputs, scripts, results for each research question |
| `artifact/paper_expected_results.json` | Values transcribed from the paper PDF |
| `artifact/run_quickstart.py` | Environment checker and entry point |

See [REQUIREMENTS.md](REQUIREMENTS.md) for architecture, software, storage,
network, and credential requirements.

Each `artifact/RQ*/README.md` explains the role of that RQ's packaged input
files, intermediates, outputs, and any manual labels used by the evaluation.

### 2. Installation

The artifact archive includes a pre-built Docker image tarball
(`rosdepauditor-artifact-image.tar.gz`). Load it, then start a container:

~~~bash
docker load -i rosdepauditor-artifact-image.tar.gz
docker run --rm -it rosdepauditor-artifact:2026-ae
~~~

The container opens at `/workspace`. No checkout, API key, or runtime download
is needed for the initial environment check.

### 3. Initial environment check

Inside the container, verify the environment:

~~~bash
python artifact/run_quickstart.py --check-environment
~~~

This checks Python 3.11+, required Python packages, and the expected
directories (`rosdep_auditor/`, `rosdep/`, `artifact/`). It does **not** run
experiments or compare paper results.

Successful output:

~~~text
Environment satisfies requirements.
~~~

If a requirement is missing, the command lists it and exits non-zero.

### 4. Smoke test

Run one RosdepAuditor command to confirm that the tool itself is working inside
the artifact environment. This first command exercises the equivalence mapper:
starting from the known Ubuntu Noble package `python3-bson`, it asks
RosdepAuditor to find packages that play the same role in the other configured
repositories.

~~~bash
python rosdep_auditor/package_distribution_detector.py \
  "('python3-bson', 'ubuntu', 'noble')"
~~~

Expected output is a `DistributionTable`: repository/version headings such as
`ubuntu_noble`, `debian_bookworm`, `arch`, `pypi`, and `alpine_edge`, each with
the mapped package name below it. For example, the packaged environment maps
Ubuntu Noble's `python3-bson` to `python-pymongo` on Arch, `pymongo` on PyPI,
and `py3-bson` on Alpine. The complete expected output is shown in
`rosdep_auditor/README.md`.

The second smoke test exercises defect diagnosis for a rosdep key. It reads the
packaged `rosdep/python.yaml` entry for `python3-bson`, checks it against the
configured repositories, and prints a markdown-style defect report:

~~~bash
python rosdep_auditor/problem_detector.py python3-bson
~~~

Expected output includes an `A.2` finding for missing platform/version coverage
and a `B.1b` finding for a package-name mismatch. The detailed expected report
is also shown in `rosdep_auditor/README.md`.

Step-by-step evaluation begins in Part II.

**Where to find more information:**
- **This archive:** [REQUIREMENTS.md](REQUIREMENTS.md) (hardware/software
  requirements), [STATUS.md](STATUS.md) (badge status), and
  [LICENSE.md](LICENSE.md) accompany this README.
- **Inside the container:** Tool API examples are in
  `rosdep_auditor/README.md`. Set `TOOL_PATH=/workspace` and
  `PYTHONPATH=/workspace` (already configured in the image) before using the
  library in your own scripts.

## Part II — Step-by-step reproduction instructions

Part II provides two complementary paths. The quick path checks paper-facing
summary values derived from the packaged artifact results. The detailed path
runs the analysis scripts for individual RQs and describes optional fresh
execution where applicable.

### Quick reproduction path

#### Check paper-facing summary values using the packaged results

The artifact includes the intermediate and result files produced for RQ1–RQ6.
To read these packaged files, derive the corresponding paper-facing summary
values, and compare them with `artifact/paper_expected_results.json`, run:

~~~bash
python artifact/run_quickstart.py
~~~

The command runs offline — it does not call LLM APIs, GitHub APIs, Repology, or
package repository endpoints. It verifies consistency between the packaged
artifact results and the expected paper-facing summary values. Successful
output ends in `PASS`; a missing or different value is reported as `MISS`,
`MISMATCH`, or `FAIL`, and the command exits nonzero.

#### How quickstart obtains each paper claim

| Claim | Paper conclusion | How `run_quickstart.py` obtains it |
| --- | --- | --- |
| C1 / RQ1 | The 863 cases yield 806 coverage and 123 correctness defects: A.1=625, A.2=181, B.1=111, B.2=12. | Reads the two classified CSVs, removes issues that overlap PRs, and recounts cases and defect types. |
| C2 / RQ2 | Maintenance costs include 248.6 h mean resolution, 3,915 comments, 96.8% manual review, 66.5% post-review iteration, and repeated-entry burdens. | Calculates durations, comment/review totals, post-review changes, rule-related changes, and repeated-key statistics from the packaged RQ1/RQ2 CSVs. |
| C3 / RQ3 | Of 1,737 entries, 1,331 (76.6%) exhibit naming inconsistency; barrier counts are 1,007/240/526/568. | Reads the packaged RQ3 summary and overlap summary, then calculates the structural/content rate. |
| C4 / RQ4 | RosdepAuditor reaches 808 exact + 1 partial matches over 854 instances (94.6% exact, 94.7% total), with no nonexistent output. | Reads every method row in `final_version.md`, compares each against the ground truth in `ground_truth_dataset_filtered.yaml`, calculates total-match rates, and checks extra/nonexistent counts from the verification JSON. |
| C5 / RQ4 | Weight sensitivity peaks at 94.6% exact / 94.7% total around w=0.6–0.7. | Reads the rows for w=0.0 through 1.0 in `w_related_ver.md`, compares each exact-match rate against the ground truth in `ground_truth_dataset_filtered.yaml`. |
| C6 / RQ5 | Diagnosis reaches 82/83 recall (98.8%) and 99/100 precision (99.0%). | Reads the total and per-type values in the packaged recall/precision report produced from the PR and manual-verification data. |
| C7 / RQ6 | Full scan finds 3,249 defects affecting 2,233/2,513 entries (88.9%); 818 B.1 cases reference nonexistent packages. | Reads type/subtype and affected-entry totals in the packaged scan report and calculates the affected-entry rate. |
| C8 / RQ6 | All 46 submitted defects were confirmed/fixed; prospective data has 33 PRs, 35 defect instances, 32/35 matches, and 21 same-package PRs. | Counts submitted defect types from `submitted_issues.csv`, then counts prospective addressed types, overlaps, and same-package PR rows from `pr_defect_types_mapping_manual.csv`. |

Reviewers who want to run the analysis for a specific RQ can use the scripts in
the detailed path below.

### Detailed step-by-step reproduction path

Each section lists the analysis script, its supplied inputs, and the expected
result. Optional fresh-execution commands are included where useful. These may
require network access, provider credentials, or additional runtime; commands
that write result files should be run in a disposable container.

#### Step 1 — Common preparation

All commands start at `/workspace`:

~~~bash
pwd
~~~

Expected `pwd` output is `/workspace`. Run live/full
commands in a disposable container. Never add credentials to the image or
repository.

#### Step 2 — RQ1 and RQ2: empirical-study claims

##### RQ1: taxonomy of defects (C1)

~~~bash
python artifact/RQ1/scripts/empirical_analysis.py
~~~

Inputs:

- `artifact/RQ1/data/pr_manual_analyzed_final.csv`
- `artifact/RQ1/data/issues_analyzed.csv`

Expected: 835 classified merged PRs plus 28 non-overlapping issues form 863
cases. The command reports A.1=625, A.2=181, B.1=111, and B.2=12. A case may
have multiple types, so category totals exceed 863. A.1+A.2=806 and
B.1+B.2=123.

The script calculates the reported counts from the supplied classified
datasets, which contain the final labels from the empirical coding process.

**Paper-aligned values:**

| Metric | Paper value | Artifact check |
| --- | ---: | ---: |
| Initial PRs mined (raw) | 1002 | 1002 |
| Initial issues mined (raw) | 534 | 534 |
| Total maintenance cases | 863 | 863 |
| Unique issues | 28 | 28 |
| PR cases | 835 | 835 |
| A.1 Missing Dependency | 625 | 625 |
| A.2 Incomplete Platform | 181 | 181 |
| B.1 Invalid Package Specification | 111 | 111 |
| B.2 Suboptimal Prioritization | 12 | 12 |
| Coverage defects | 806 | 806 |
| Correctness defects | 123 | 123 |

The defect-type classification of PRs and issues involves manual annotation by
the paper authors and cannot be re-derived automatically. Reviewers can use the
packaged classified datasets (`pr_manual_analyzed_final.csv`,
`issues_analyzed.csv`) to reproduce the paper-facing summary values.

**Optional — Re-collect raw data from GitHub.** The raw 1,002 PRs (label:rosdep,
comments ≥1, created ≤2025-04-30) and 534 issues (created ≤2025-04-30) can be
re-collected from the GitHub Search API. Requires `GITHUB_TOKEN`:

~~~bash
python artifact/RQ1/scripts/mine_raw_data.py
~~~

Outputs: `raw_prs.csv` and `raw_issues.csv` under `artifact/RQ1/data/`.
The re-collected counts may drift slightly if the repository state has changed
since the paper snapshot.

##### RQ2: maintenance bottleneck (C2)

Run the RQ2 analysis:

~~~bash
python artifact/RQ2/scripts/RQ2.py
~~~

Inputs are `comments_in_prs.csv`,
`commits_after_review.csv`, `pr_modified_keys.csv`,
`pr_chained_modification.csv`, and
`key_packages.csv` under `artifact/RQ2/data/`, plus the
RQ1 PR CSV.

Expected: 248.6 hours average resolution; 3,915 comments (4.69/PR); manual
comments in 808/835 PRs (96.8%); 555/835 post-review iterations (66.5%);
402/555 rule-related iterations (72.4%); 990 repeatedly modified entries out of
1,921 (51.5%); 2.94 updates per repeated entry; and 137/990 repeated within 30
days (13.8%). The package-name comparison is also checked from packaged data:
302/456 PRs that introduced raw package-name differences had rule-related
follow-up modifications (66.2%), versus 100/379 PRs without such differences
(26.4%).

The packaged CSVs contain all rows used in the analysis. Re-collecting missing
GitHub data requires a `GITHUB_TOKEN`; use a disposable container for
that mode because the script can update the CSV files.

**Paper-aligned values:**

| Metric | Paper value | Artifact check |
| --- | ---: | ---: |
| PRs | 835 | 835 |
| Average resolution time | 248.6 hours | 248.6 hours |
| Review comments | 3915 | 3915 |
| Average comments per PR | 4.69 | 4.69 |
| PRs with manual review | 808 | 808 |
| Manual review rate | 96.8% | 96.8% |
| PRs with post-review iteration | 555 | 555 |
| Post-review iteration rate | 66.5% | 66.5% |
| Rule-related iteration PRs | 402 | 402 |
| Rule-related iteration rate | 72.4% | 72.4% |
| Unique entries | 1921 | 1921 |
| Entries modified by multiple PRs | 990 | 990 |
| Repeated-entry rate | 51.5% | 51.5% |
| Average updates per repeated entry | 2.94 | 2.94 |
| Short-period repeated entries | 137 | 137 |
| Short-period repeated-entry rate | 13.8% | 13.8% |

#### Step 3 — RQ3: barriers to automation (C3)

~~~bash
python artifact/RQ3/scripts/RQ3.py
~~~

Inputs include the bundled `rosdep/` snapshot and packaged RQ3
intermediates. Expected: 1,737 entries; 1,331 naming-inconsistent entries
(76.6%); affixation=1,007; evolution=240; packaging policy=526; and
structural/content inconsistency=568 (42.7% of 1,331).

`RQ3.py` recalculates the naming summaries and the 568
structural/content count using the packaged `no_overlap_pairs.csv`
intermediate. Rebuilding that intermediate retrieves package file lists from
online repositories and can take substantially longer. The command below
rewrites `artifact/RQ3/data/no_overlap_pairs.csv`; successful output ends with
the number of zero-overlap package-file-list pairs saved to that file.

~~~bash
python artifact/RQ3/scripts/filelist_similarity_analysis.py
~~~

**Paper-aligned values:**

| Metric | Paper value | Artifact check |
| --- | ---: | ---: |
| Entries analyzed | 1737 | 1737 |
| Entries with inconsistent naming | 1331 | 1331 |
| Inconsistency rate | 76.6% | 76.6% |
| Divergent affixation rules | 1007 | 1007 |
| Repository evolution | 240 | 240 |
| Differing packaging policies | 526 | 526 |
| Structural/content overlap entries | 568 | 568 |
| Structural/content overlap rate | 42.7% | 42.7% |

#### Step 4 — RQ4: equivalence mapping and sensitivity (C4–C5)

The ground truth is `ground_truth_dataset_filtered.yaml`
(112 entries / 854 platform instances), filtered from the full
`ground_truth_dataset.yaml` (135 entries) under `artifact/RQ4/data/`.
Both are built automatically by `build_ground_truth.py` from merged rosdep PRs.

RQ4 uses the following method names in commands and result directories:

| Name in command/output | Meaning | Main output |
| --- | --- | --- |
| `detector` | RosdepAuditor's `PackageDistributionDetector`, which starts from a known package and infers equivalent packages across repositories. In RQ4 this mode is used for the `w` sensitivity study. | `artifact/RQ4/data/result/detector_w00/` through `detector_w10/`, plus a timestamped `comparison_*.md` report |
| `baseline` | Non-LLM comparison tools: `RepologyMapper` and `RosdepCIMapper`. | `artifact/RQ4/data/result/mapper/` and `artifact/RQ4/data/result/rosdep_ci/` |
| `llm` | LLM-based detector presets configured in `rosdep_auditor/tools/llm_caller.py`; preset names use filesystem-friendly forms such as `claude-opus-4-5`, while the paper reports the model as Claude-Opus-4.5. | `artifact/RQ4/data/result/llm_*/` |
| `compare` | Combined paper comparison at `w=0.7`, using detector, baselines, and LLM methods. | `artifact/RQ4/data/result/combined/` and the markdown report later copied to `final_version.md` |

Regenerate summary tables from packaged method outputs. Use `--force` only to
refresh the underlying detections instead of reusing the packaged per-entry
results.

**C4 — Multi-method comparison (w=0.7)** reads the packaged per-entry JSON
outputs, compares each method against `ground_truth_dataset_filtered.yaml`, and
generates a timestamped `comparison_*.md` report. The `mv` command renames that
timestamped report to the stable paper-facing file `final_version.md`:

~~~bash
python artifact/RQ4/scripts/run_distribution_comparison.py --mode compare
mv artifact/RQ4/data/comparison_*.md artifact/RQ4/data/final_version.md
~~~

**C5 — Weight sensitivity (w=0.0…1.0)** runs the detector configuration for
eleven weights (`0.0`, `0.1`, ..., `1.0`) or reuses the packaged
`detector_w*` JSON files. It writes one timestamped markdown report with the
exact/partial/no-match counts for each weight; the `mv` command stores that
report as `w_related_ver.md`:

~~~bash
python artifact/RQ4/scripts/run_distribution_comparison.py --mode detector
mv artifact/RQ4/data/comparison_*.md artifact/RQ4/data/w_related_ver.md
~~~

Then verify and plot:

~~~bash
python artifact/RQ4/scripts/verify_all_llms.py --extra-nonexistent
python artifact/RQ4/scripts/RQ4_plot.py --no-show
~~~

The verification command checks extra outputs and nonexistent-package counts
from the packaged comparison results, writing JSON summaries under
`artifact/RQ4/data/verification_results/`. The plot command reads
`w_related_ver.md` and writes the sensitivity figure `w_sensitivity.pdf`.
Primary evaluation inputs are `ground_truth_dataset_filtered.yaml`,
`data/result/`, and `data/verification_results/`.


Optional fresh execution (network / credentials required; the packaged
`ground_truth_dataset.yaml` can be used directly to skip this step):

Rebuild the ground-truth dataset from scratch via GitHub Search API
(requires `GITHUB_TOKEN`):

~~~bash
python artifact/RQ4/scripts/build_ground_truth.py --all
~~~

This fetches merged rosdep PRs created after `2025-04-30` and merged before
`2025-09-30`, extracts modified rosdep keys from each PR diff, and produces
`ground_truth_dataset.yaml` (135 entries). The `--filter` step keeps only
entries mapped to `ubuntu_noble` with installable packages across ≥2 distinct repository platforms,
producing `ground_truth_dataset_filtered.yaml` (112 entries / 854 platform
instances).

Refresh the method outputs. These commands overwrite or add per-entry JSON
files under `artifact/RQ4/data/result/` and also create a new timestamped
`artifact/RQ4/data/comparison_*.md` plus a YAML summary under
`artifact/RQ4/data/reports/`:

~~~bash
python artifact/RQ4/scripts/run_distribution_comparison.py --mode detector --force
python artifact/RQ4/scripts/run_distribution_comparison.py --mode baseline --force
python artifact/RQ4/scripts/run_distribution_comparison.py --mode llm --force
~~~

`--mode detector --force` refreshes RosdepAuditor outputs for each weight
(`detector_w00` through `detector_w10`). `--mode baseline --force` refreshes
Repology and RosdepCI outputs (`mapper/` and `rosdep_ci/`). `--mode llm --force`
refreshes LLM outputs for the configured model presets. Without `--force`,
existing per-entry JSON files are reused. Forced detector and baseline runs use
the artifact's frozen ground-truth inputs while querying current package indexes
or Repology; LLM mode requires provider credentials and API usage, and rerun
outputs may differ from the packaged results because LLM-based reruns
inherently involve some service-side variability over time.
These commands can take substantially longer and write new timestamped reports.
See `artifact/RQ4/README.md` for detailed script usage and data descriptions.

**Paper-aligned values — method comparison (w=0.7):**

| Method | Exact | Partial | None | Exact rate | Total rate | Extra total | Nonexistent | Nonexistent rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PackageDistributionDetector (w=0.7) | 808 | 1 | 45 | 94.6% | 94.7% | 334 | 0 | 0.0% |
| RosdepCIMapper | 577 | 0 | 277 | 67.6% | 67.6% | 129 | 0 | 0.0% |
| RepologyMapper | 173 | 522 | 159 | 20.3% | 81.4% | 109957 | 0 | 0.0% |
| LLMDetector (gemini-3-pro) | 785 | 4 | 65 | 91.9% | 92.4% | 362 | 138 | 38.1% |
| LLMDetector (glm-4.7) | 766 | 0 | 88 | 89.7% | 89.7% | 355 | 150 | 42.3% |
| LLMDetector (Claude-Opus-4.5) | 702 | 0 | 152 | 82.2% | 82.2% | 282 | 70 | 24.8% |
| LLMDetector (deepseek-v3.2) | 700 | 0 | 154 | 82.0% | 82.0% | 460 | 257 | 55.9% |

**Paper-aligned values — weight-sensitivity exact rates:**

| w | Paper value | Artifact check |
| ---: | ---: | ---: |
| 0 | 87.0% | 87.0% |
| 0.1 | 89.9% | 89.9% |
| 0.2 | 91.2% | 91.2% |
| 0.3 | 92.2% | 92.2% |
| 0.4 | 93.0% | 93.0% |
| 0.5 | 94.3% | 94.3% |
| 0.6 | 94.6% | 94.6% |
| 0.7 | 94.6% | 94.6% |
| 0.8 | 94.5% | 94.5% |
| 0.9 | 94.4% | 94.4% |
| 1 | 90.0% | 90.0% |

#### Step 5 — RQ5: defect diagnosis (C6)

~~~bash
python artifact/RQ5/scripts/run_experiments.py analyze
~~~

Inputs are `rq5_database.csv` and
`rosdepauditor_new_type_analysis_results_manual.csv`.
The RQ5 database is the frozen PR candidate skeleton with manually filled
`classification_types`. The run step converts those labels to `expected_types`
and adds RosdepAuditor `actual_types`; the paper-facing calculation reads the
manually verified results CSV. The `analyze` command writes
`artifact/RQ5/data/defect_detection_recall.md`, whose summary contains the
overall recall/precision values and the per-type table below.

**Paper-aligned values — overall:**

| Metric | Paper value | Artifact check |
| --- | ---: | ---: |
| Dataset PRs | 75 | 75 |
| Ground-truth defects | 83 | 83 |
| Reported defects | 100 | 100 |
| Verified detections | 99 | 99 |
| True positives | 82 | 82 |
| Recall | 98.8% (82/83) | 98.8% |
| Precision | 99.0% (99/100) | 99.0% |

**Per-type values:**

| Type | Reported | Ground truth | Recall | Precision |
| --- | ---: | ---: | ---: | ---: |
| A.1 | 48 | 48 | 100.0% | 100.0% |
| A.2 | 26 | 16 | 100.0% | 100.0% |
| B.1 | 16 | 11 | 90.9% | 93.8% |
| B.2 | 10 | 8 | 100.0% | 100.0% |
| Total | 100 | 83 | 98.8% (82/83) | 99.0% (99/100) |

Optional fresh end-to-end path (network / credentials required; uses a
disposable container to avoid overwriting packaged files. Steps 2 and 4 involve
manual annotation — reviewers can skip them and use the packaged
`rq5_database.csv` and `rosdepauditor_new_type_analysis_results_manual.csv`
directly):

1. Rebuild the PR candidate skeleton:
~~~bash
python artifact/RQ5/scripts/build_rq5_database.py
~~~
This writes `rq5_database.csv` with PR metadata and automatically extracted
`modified_keys`. The `classification_types` column is left blank for manual
ground-truth annotation.

2. Fill ground-truth classifications: manually fill `classification_types` in
`rq5_database.csv`. This column is the human-labeled defect type set (e.g.,
`A.1`, `A.2`, `B.1`).

3. Run RosdepAuditor on the classified PRs:
~~~bash
python artifact/RQ5/scripts/run_experiments.py run
~~~
This copies `classification_types` into `expected_types` and adds
RosdepAuditor's `actual_types` in
`rosdepauditor_new_type_analysis_results.csv`.

4. Verify RosdepAuditor outputs: review the generated analysis results and save
the checked version as `rosdepauditor_new_type_analysis_results_manual.csv`,
adding `verified_types` and `verified_reasons`.

5. Generate the recall/precision table:
~~~bash
python artifact/RQ5/scripts/run_experiments.py analyze
~~~
This reads the manually verified CSV and rewrites
`artifact/RQ5/data/defect_detection_recall.md`.

See `artifact/RQ5/README.md` for a detailed description of each step and the
input/output files.

#### Step 6 — RQ6: full-index usefulness and validation (C7–C8)

RQ6 has two separate flows. The full-index scan is entry-level and does not
use PR data: it summarizes `defects.csv`, which was produced from the official
rosdistro central index state used in the paper (August 2025, commit
`592178c`). The prospective validation is PR-level: run
`build_rq6_prospective_database.py` to create the 33-row
`rq6_prospective_database.csv` and a blank `pr_defect_types_mapping.csv`
template, then manually complete that template as
`pr_defect_types_mapping_manual.csv`. Paper-facing prospective counts are
computed from the manual file.

Regenerate summaries:

~~~bash
python artifact/RQ6/scripts/run_experiment_usefulness.py analyze
python artifact/RQ6/scripts/generate_pr_defect_report.py --analyze artifact/RQ6/data/pr_defect_types_mapping_manual.csv
~~~

The first command reads the packaged full-index scan
`artifact/RQ6/data/defects.csv` and rewrites
`artifact/RQ6/data/defect_distribution_analysis.md`. Expected: 3,249 potential
defects across 2,233 of 2,513 entries (88.9%), with A.1=0, A.2=1,820,
B.1=1,139, and B.2=290. The subtype table contains B.1a=818 packages not found
in their repository.

The second reads the manually completed 33-row prospective mapping and reports
21 A.2 + 14 B.1 = 35 addressed defect instances. Of these, 20 A.2 + 12 B.1 =
32 (91.4%) overlap RosdepAuditor. The paper-facing same-package result is 21
overlapping PRs with `use_our_packages=1`. The blank
`pr_defect_types_mapping.csv` is the template generated from
`rq6_prospective_database.csv`; the packaged submitted-issue data records the
46 submitted defects and their confirmation/fix outcomes.

**Paper-aligned values — defect distribution:**

| Metric | Paper value | Artifact check |
| --- | ---: | ---: |
| Total defects | 3249 | 3249 |
| Entries affected | 2233 | 2233 |
| Affected-entry rate | 88.9% | 88.9% |
| A.1 defects | 0 | 0 |
| A.2 defects | 1820 | 1820 |
| B.1 defects | 1139 | 1139 |
| B.2 defects | 290 | 290 |

**Paper-aligned values — prospective validation:**

| Metric | Paper value | Artifact check |
| --- | ---: | ---: |
| Reported defects confirmed | 46 | 46 |
| Prospective PRs | 33 | 33 |
| Prospective A.2 defects | 21 | 21 |
| Prospective B.1 defects | 14 | 14 |
| Prospective exactly identified defects | 32 | 32 |
| Prospective fixes using the same packages | 21 | 21 |

Optional live paths (network required; use a disposable container for runs that
write files):

**Full-index scan:**
~~~bash
# Dry-run preview on a subset (e.g., first 50 entries) — no files written
python artifact/RQ6/scripts/run_experiment_usefulness.py check --dry-run --end=50

# Full scan — audits every entry against live indexes; can take hours
python artifact/RQ6/scripts/run_experiment_usefulness.py check
~~~

**Prospective validation end-to-end** (step 2 involves manual annotation — use
the packaged `pr_defect_types_mapping_manual.csv` to skip it):
1. Build the prospective PR skeleton:
~~~bash
python artifact/RQ6/scripts/build_rq6_prospective_database.py
~~~
2. Manually fill `defect_types_addressed_in_pr`,
`defect_types_detect_from_keys`, `use_our_packages`, and `description` in the
blank `pr_defect_types_mapping.csv` template. Save as
`pr_defect_types_mapping_manual.csv`.
3. Generate the prospective-validation summary:
~~~bash
python artifact/RQ6/scripts/generate_pr_defect_report.py --analyze artifact/RQ6/data/pr_defect_types_mapping_manual.csv
~~~

See `artifact/RQ6/README.md` for a detailed description of each flow and the
input/output files.

#### Reuse and extension

Tool API examples are in `rosdep_auditor/README.md`.
For a new index snapshot, use a disposable workspace, replace the snapshot,
clear method caches, and expect live results to differ. The writable container
cache is `/home/appuser/cache/rosdistro_watcher`.

### Full reproduction notes

The artifact and its included cache files are frozen as of **June 2026**. The
results reported in the paper reflect that point in time. Some experiments are
intentionally expensive and rely on external services:

- **RQ4 LLM modes** use external model APIs. To save tokens, reviewers can
  evaluate from the packaged `artifact/RQ4/data/result/` and
  `artifact/RQ4/data/verification_results/` outputs, or set
  `--force` to re-run with their own API credentials.
- **RQ4 baseline and detector** reruns may contact external package indexes.
- **RQ5 `run`** mode and parts of **RQ2** collection can require
  GitHub API access.
- **RQ6 full `check`** over all entries can be long. The
  artifact includes `artifact/RQ6/data/defects.csv` and
  `artifact/RQ6/data/defect_distribution_analysis.md` for inspection.
- **RQ3 `filelist_similarity_analysis.py`** rebuilds the
  `no_overlap_pairs.csv` intermediate using package repository
  metadata and can take a long time.

For long-running commands, reviewers can run a subset, for example:

~~~bash
python artifact/RQ6/scripts/run_experiment_usefulness.py check --dry-run --end=50
~~~

To run any command against the current (live) state of package indexes instead
of the frozen cache, set `ROSDEP_AUDITOR_USE_CACHE=false`. Use this only for
commands that query repository metadata, such as a dry-run subset of the RQ6
full-index checker:

~~~bash
ROSDEP_AUDITOR_USE_CACHE=false python artifact/RQ6/scripts/run_experiment_usefulness.py check --dry-run --end=50
~~~

When this variable is set to `false`, all cached data is ignored and
fetched from live sources (Repology, package repositories, etc.). Results will
reflect the current state and can differ from the paper because the underlying
repositories and services continue to evolve.

### Paper claims not covered by this artifact

RQ1, RQ5, and RQ6 each involve a manual analysis step that annotates
individual PRs — specifically, determining which defect types each PR
introduces or addresses. These annotations cannot be re-derived automatically
because they require human judgment about the nature and scope of each change.

| RQ  | Manual step | What it annotates |
| --- | --- | --- |
| RQ1 | Defect-type classification of 835 merged PRs and 28 issues | Each PR/issue labeled with defect types (A.1, A.2, B.1, B.2) |
| RQ5 | Per-PR ground-truth labels in `rq5_database.csv` | `classification_types` for 75 candidate PRs |
| RQ6 | Prospective-validation mapping in `pr_defect_types_mapping_manual.csv` | For each of 33 prospective PRs: which defect types were addressed, whether RosdepAuditor detected them, and whether the fix reused our suggested packages |

The optional data-collection script (`artifact/RQ1/scripts/mine_raw_data.py`)
can re-collect the initial 1,002 PRs and 534 issues via GitHub Search API, and
the RQ4 ground-truth dataset can be rebuilt from scratch with
`artifact/RQ4/scripts/build_ground_truth.py`. The RQ5 and RQ6 candidate
skeletons can similarly be regenerated by their respective `build_*.py` scripts.
However, the downstream annotation of which defect types each PR involves, and
the manual verification of RosdepAuditor's outputs against those annotations,
must be performed by a human.

All other claims and results in RQ1–RQ6 are reproducible from the packaged data
and scripts.

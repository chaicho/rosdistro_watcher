# RosdepAuditor Distribution Detector Web App

A small Flask/Bootstrap interface for finding equivalent packages across the repositories configured for RosdepAuditor.

## Run

First enter the configured Docker environment described in the repository
README. Then run:

```bash
python web_app/app.py
```

Then open:

```text
http://localhost:5000
```

## API

### `GET /api/repositories`

Returns repository names and versions from `rosdep_auditor/configs/config_mini.yaml`.

### `POST /api/detect`

Runs package distribution detection.

```json
{
  "package_name": "libicu-dev",
  "repo_name": "ubuntu",
  "repo_version": "jammy",
  "method": "standard",
  "options": {
    "top_k": 5,
    "score_threshold": 0,
    "show_scores": true,
    "use_name_matches": true,
    "use_source_related": true,
    "use_cache": true,
    "weight": 0.7,
    "scorer": "default"
  }
}
```

Supported methods are `standard`, `llm`, and `multi_mode`. Multi-mode returns standard results, LLM results, and all candidates when available.

### `GET /health`

Reports whether the Flask app can initialize `PackageDistributionDetector`.

# Artifact Requirements

## Architecture

The artifact is packaged for **x86-64 (linux/amd64)**. The published Docker
image and all reported results use this architecture.

## Hardware

- Disk: at least 25 GB free for the container image and any generated outputs.
- Memory: normal container memory is sufficient for the quickstart and packaged
  result checks. Fresh full-index scans or model experiments may need additional
  RAM.
- No GPU or non-commodity peripheral is required.

## Software

- **Docker** (or a compatible container runtime). See the `README.md`
  included alongside this file for image load and run instructions.
- The container provides **Python 3.11** and all required dependencies.
- No host-level Python or package installation is needed.

## Dependencies

- `rosdep_auditor/requirements.txt` — core library Python dependencies.
- `docker/artifact-requirements.txt` — additional Python dependencies for artifact scripts.

## Network

The artifact caches the state of all referenced repositories and external tool
interactions as of **June 2026** to meet the ISSTA Artifact Evaluation
requirement of self-containedness. All quickstart checks and packaged-result
verification paths operate **offline**. Internet access is needed when extending the artifact beyond the supplied data — for example, applying RosdepAuditor to a new index snapshot or querying live package indexes for repositories not covered by the artifact.


# RosdepAuditor Core Library

The core package checks rosdep rules against package repositories and identifies
equivalent packages across distributions.

Use the Docker environment configured in the [repository README](../README.md).
Commands in this document assume that environment is already active.

## Features

- Package-existence verification for deb, rpm, apk, pacman, PyPI, Gentoo, and NixOS repositories.
- Cross-distribution equivalence mapping.
- Rosdep defect detection:
  - A.1: missing dependency definition.
  - A.2: incomplete platform or version coverage.
  - B.1: invalid package specification.
  - B.2: suboptimal repository prioritization.

## Configuration

Repository sources, target versions, architectures, and name-normalization rules
are defined in `configs/*.yaml`. The examples below use
`config_usefulness.yaml`.

## Python API

The main entry points are:

- `problem_detector.load_yaml_file` and `build_rosdep_database` for loading a rosdep YAML snapshot.
- `problem_detector.check_entry_all_defects` for auditing one key.
- `package_distribution_detector.PackageDistributionDetector` for cross-repository mapping.

## Example: Detect a Package Distribution

Package distribution detection starts from one known package in one repository
and finds packages with the same role in the other configured repositories. In
this example, `python3-bson` from Ubuntu Noble is the specific baseline package.

There are two equivalent ways to run it.

### Python API

Pass the baseline as a `(package_name, repository, version)` tuple. A keyword is
not needed when a specific package is provided.

```python
from rosdep_auditor.config import load_config
from rosdep_auditor.package_distribution_detector import (
    PackageDistributionDetector,
)

config = load_config("rosdep_auditor/configs/config_usefulness.yaml")
detector = PackageDistributionDetector(config)
distribution = detector.detect_distribution(
    specific_package=("python3-bson", "ubuntu", "noble"),
)
print(distribution)
```

### Command line

The command-line form accepts the same three-value tuple and uses
`config_usefulness.yaml` by default.

```bash
python rosdep_auditor/package_distribution_detector.py \
  "('python3-bson', 'ubuntu', 'noble')"
```

Both forms produce the same `DistributionTable`. The actual result is:

```text
opensuse_15.2:
  python3-pymongo
ubuntu_jammy:
  python3-bson
arch:
  python-pymongo
nixos_unstable:
  python314Packages.pymongo
debian_bookworm:
  python3-bson
ubuntu_noble:
  python3-bson
pypi:
  pymongo
alpine_edge:
  py3-bson
rhel_8:
  python3-bson
fedora_42:
  python3-bson
debian_trixie:
  python3-bson
fedora_41:
  python3-bson
rhel_9:
  python3-bson
```

Each heading is a repository and version; the indented value is the package
selected as equivalent to Ubuntu Noble's `python3-bson`. For example,
`alpine_edge` uses `py3-bson`, while Arch uses `python-pymongo`. This operation
only maps equivalent packages; it does not inspect a rosdep rule.

## Example: Audit One Rosdep Key

Auditing checks an existing rosdep rule against the configured repositories. It
can report missing platform coverage, nonexistent or mismatched package names,
and suboptimal package-manager choices. This example audits the
`python3-bson` key from `rosdep/python.yaml` with `config_usefulness.yaml`.

There are again two equivalent ways to run it.

### Python API

```python
from rosdep_auditor.config import load_config
from rosdep_auditor.problem_detector import (
    build_rosdep_database,
    check_entry_all_defects,
    format_defects_to_markdown,
    load_yaml_file,
)

config = load_config("rosdep_auditor/configs/config_usefulness.yaml")
database = build_rosdep_database(config, load_yaml_file("rosdep/python.yaml"))
defects = check_entry_all_defects(config, database, "python3-bson")
print(format_defects_to_markdown(defects, "python3-bson"))
```

### Command line

The command-line form uses `rosdep/python.yaml` and
`config_usefulness.yaml` by default.

```bash
python rosdep_auditor/problem_detector.py python3-bson
```

If the key is in another central-index YAML, pass it with `--rosdep-file`, for
example `--rosdep-file rosdep/base.yaml`.

Both forms produce the same formatted defect report. The actual result is:

```text
### Entry: `python3-bson`

- **Type A.2**: Missing platform/version in entry
  - Entry 'python3-bson' missing platform 'alpine'; suggested package: 'py3-bson'
  - Entry 'python3-bson' missing platform 'arch'; suggested package: 'python-pymongo'
  - Entry 'python3-bson' missing platform 'opensuse'; suggested package: 'python3-pymongo'

- **Type B.1b**: Package name differs from expected
  - Package 'python3Packages.bson' differs from expected 'python314packages.pymongo' in nixos unstable
```

`A.2` means the rosdep rule lacks entries for repositories where the detector
found an equivalent package; each line includes the suggested package name.
`B.1b` means the package already recorded for NixOS differs from the package
selected from the current NixOS repository metadata.

## Reusing RosdepAuditor

RosdepAuditor can be reused beyond the worked examples. You can audit other
rosdep keys, start package-distribution detection from other packages, and
retarget the analysis to different OS package repositories or releases. For
package-distribution detection, a known Ubuntu package is usually the best
starting point, because ROS dependency rules are commonly authored around
Ubuntu first and the detector gives Ubuntu the highest baseline priority.

Repository coverage is easy to retarget: `configs/*.yaml` decides which package
repositories and OS releases RosdepAuditor checks. Even for a newer release
that was not part of the original experiment setup, such as the recently
released Ubuntu 26.04, RosdepAuditor can include it as long as the release is
added in the same format. For Ubuntu 26.04, add its release codename
(`resolute`):

```yaml
supported_versions:
  ubuntu:
  - jammy
  - noble
  - resolute

supported_arches:
  ubuntu:
  - amd64
```

This works as long as the corresponding `package_sources` URL template supports
that release. The knowledge database has already been built for entries covered
by the current central index; using packages, keys, or OS repositories outside
that index may require rebuilding it, which can take time and may produce
results that differ from the packaged artifact outputs.

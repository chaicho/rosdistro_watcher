# Artifact Status

## Badges Applied For

**Available.** The artifact has been archived on Zenodo with a DOI. The DOI link is included in the data availability statement at the end of the paper.

**Functional.** We apply for the Reusable badge, which entails the Functional level under the ISSTA 2026 artifact evaluation criteria. The artifact is documented, consistent, complete, and exercisable, and it includes verification evidence: the offline quickstart checks the packaged artifact results against the expected paper-facing summary values and exercises the `rosdep_auditor/` core tool on representative package-detection and defect-audit tasks.

**Reusable.** The artifact is carefully documented and well-structured to facilitate reuse and repurposing. It is organized by research question (`artifact/RQ1/`–`artifact/RQ6/`), with per-RQ README files detailing inputs, outputs, and regeneration commands. `rosdep_auditor/` is a reusable tool for automated rosdep defect detection, and its equivalent-package detection can also be reused beyond ROS for matching packages across repositories, independent of the paper experiments.  Its README documents the required dependencies and configuration, and a self-contained Docker environment is also provided, giving the full runtime with no host-level dependencies beyond Docker.

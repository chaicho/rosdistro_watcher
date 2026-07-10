# Defect Distribution Analysis

---

## Primary Defect Categories (A.1, A.2, B.1, B.2)

| Type | Count |
| --- | --- |
| A.1 | 0 |
| A.2 | 1820 |
| B.1 | 1139 |
| B.2 | 290 |
| **Total** | 3249 |

## Sub-Type Breakdown (B.1a, B.1b)

| Type | Count |
| --- | --- |
| B.1a | 818 |
| B.1b | 464 |
| **Total** | 1282 |

_B.1 counts unique entries with at least one B.1 sub-type. An entry can carry both B.1a and B.1b: 143 entries have both, so the sub-type sum (818 + 464 = 1282) exceeds the deduplicated B.1 count (1139)._

## Entry Perspective

| Metric | Count |
| --- | --- |
| Total Entries in DB | 2513 |
| Entries With Defects | 2233 |

## Defect Type Descriptions

- **A.1**: Entry not found in ROSdep database
- **A.2**: Missing platform/version in entry
- **B.1**: Repository availability issues (aggregated from B.1a and B.1b)
- **B.1a**: Package not found in repository
- **B.1b**: Package name differs from expected
- **B.2**: Suboptimal package manager usage

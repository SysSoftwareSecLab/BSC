# Final Build Projection

This directory contains the anonymous 114-file verifier projection used by the
accepted portable revalidation, together with three reviewer-support
native-probe source/build files. The native records separately bind the exact
final Python modules and the accepted persistent-server binary. This is not a
Git checkout or repository tag.

## Locked identity

- Identity algorithm: `sha256-canonical-file-manifest/v1`
- Candidate files: 114
- Python source files: 96
- Frozen controlled-contract and trajectory files: 18
- Anonymous public-projection root SHA-256:
  `28866e84438e8e72e46b5a8a0724587aaea2b6f46bbb37450c0860c38fd7d419`
- Byte-exact public files: 109
- Disclosed privacy-redacted public files: 5

The 114 files are assembled from four hash-checked components:

| Component | Files | Release treatment |
|---|---:|---|
| Final core verifier | 33 | Included as exact-content source files |
| Accepted Stage-4 toolchain | 62 | 57 exact files plus five disclosed privacy-only projections |
| Corrected controlled property adapter | 1 | Included at its locked module path |
| Frozen controlled contracts and trajectories | 18 | Included in the candidate tree and mirrored under `contracts-and-trajectories/mac-controlled-freeze/` |

No component is accepted by filename alone. `BUILD_MANIFEST.json` records the
public-projection digest of every released candidate-relative path.
`PRIVACY_REDACTIONS.json` identifies the five changed paths without repeating
the removed identifiers or any undistributed pre-redaction identity. The five files contain six replacements: four
reviewer/authorization labels, one private authorization-source description,
and one private guide-filename literal.  File lengths are preserved, and no
executable branch, input, budget, oracle rule, or verdict formula is changed.

This distinction is deliberate. The public root identifies exactly the bytes
distributed to anonymous reviewers. Private pre-redaction identities are not
part of this selective disclosure, because they add no reviewer-side byte check
and could confirm guessed identifying text. The release therefore identifies
the anonymous final build itself and does not claim that all 114 files are
byte-identical to an undistributed private tree.

## Layout

```text
final-build/
  BUILD_MANIFEST.json
  PAYLOAD.sha256
  PRIVACY_REDACTIONS.json
  SOURCE_BUILD_BINDING.json
  pyproject.toml
  native-probe-source/
  03_experiments/contracts/EXP-S4-002_CONTROLLED_UNSEEN_FORMAL_FREEZE/
  src/bisafecode/
  tools/verify_candidate_projection.py
```

The `03_experiments/...` subtree is intentionally retained inside the final
build because the locked controlled-oracle modules resolve that candidate-
relative contract path at runtime.  A byte-identical mirror is exposed in the
reviewer-facing `contracts-and-trajectories/` directory.  Do not edit either
copy independently.  `pyproject.toml`, the documentation, and the verification
utility, native-probe source, and source/build binding are release-support
files; they are not counted as part of the locked 114-file scientific candidate.

## Verify this projection

From the artifact root, run:

```bash
python3 final-build/tools/verify_candidate_projection.py --artifact-root .
```

Expected result:

```text
PASS: anonymous 114-file projection root 28866e84438e8e72e46b5a8a0724587aaea2b6f46bbb37450c0860c38fd7d419
PASS: 109 byte-exact files + 5 disclosed privacy redactions
PASS: 19 imported modules are byte-exact and exclude all redacted paths
PASS: 2 published baseline duplicates match their redacted source modules
PASS: 18 controlled-contract mirror files
```

The verifier rejects missing, extra, changed, non-regular, or symlinked
candidate payloads. It reconstructs the public canonical root, checks the
109/5 partition, validates the disclosed redaction paths, confirms the 19
recorded imported modules, and checks the two published baseline convenience
copies. It checks only released bytes; undistributed private identities are
outside this reviewer-facing package.

## Scientific boundary

The public root records which anonymous implementation bytes were selected.
The four modules containing redacted reviewer/authorization metadata were not
among the 19 modules actually imported during the Mac R2 cohort revalidation.
The module containing the guide-filename reference was also absent.  This establishes
non-import for that run only.  If a redacted pipeline is executed from this
public projection, its emitted provenance text and downstream record digest
may differ, although its verification logic is unchanged.

A hash match alone does not prove a scientific verdict; the per-case inputs,
oracle records, and execution results provide that evidence elsewhere in the
artifact.  The build implements bounded, model-relative checks and is not a
general physical-safety certificate.

## Distribution boundary

The locked 114-file candidate consists of Python and JSON files. This directory
also contains reviewer-support documentation, build metadata, CMake input, and
two C++ probe sources. It contains no Git metadata, captured system library,
compiled executable, robot mesh, media, virtual environment, or private
correspondence. External runtime dependencies are not redistributed here.

No project-wide open-source license is granted. These files are disclosed for
double-anonymous review under the access terms of the review repository; see
`../LICENSE_STATUS.md`. Content identity and redistribution permission are
separate decisions.

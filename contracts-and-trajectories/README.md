# Contracts and Trajectories

This directory contains the reviewer-visible frozen inputs used by the
portable and native evidence lanes.

- `mac-controlled-freeze/` is the byte-checked 18-file contract and trajectory
  mirror used by the portable controlled lane.
- `table-iii/` contains the six-workload schedule and H/R/C programs,
  trajectories, attachments, and checked-pair table used by the Table III
  reference evidence.
- `c115/` contains the 15-slot campaign contracts, controlled programs,
  OpenArm URDF/SRDF, and the collision meshes needed by the native model.
- `qualification/` contains the frozen reference contract used by
  the final contract and distance-error qualifications.
- `external-retrieval/` records upstream and Apache-2.0 license information for
  the included OpenArm assets.
- `NATIVE_INPUT_INDEX.json` gives the artifact-relative path, byte count, and
  SHA-256 for every selected native input, model, license, and probe-source
  file.

Internal source paths were rebound to reviewer-visible paths when the same
bytes are included. References to omitted calibration or giant raw evidence
are explicitly marked as omitted; they are not left as broken private-path
claims. Semantic trajectory/program identifiers retained in reference result
records are explained in `../final-build/HASH_AND_IDENTITY_BOUNDARY.md`.

# C115 Frozen Inputs and OpenArm Model

`inputs/campaign/` contains the declared error budget, pair scope, observer
rules, five-placement resource bound, contract, and fixed 15-slot schedule.
`inputs/controlled-programs/` contains P044 and its two retained unsafe
controls. `model/` contains the bimanual OpenArm URDF/SRDF and the 11 collision
meshes referenced by the native checks.

`C115_INPUT_BINDINGS.json` maps C, H, and R to the selected public program,
schedule, model, policy, observer, placement, dense-summary, and historical
clearance records. Every entry carries a public path and file SHA-256. The map
does not include the internal campaign wrapper or its runtime-generated gripper
trajectory digests.

Internal workflow locators were replaced by included artifact-relative paths
where the same public bytes exist. Locators for omitted historical calibration
or raw evidence are explicitly marked `OMITTED_COMPACT_HISTORICAL_EVIDENCE`.
This release-only path normalization does not change a contract value,
trajectory point, distance, verdict, or historical outcome.

Third-party provenance and Apache-2.0 license texts are under
`../external-retrieval/`. Selected file identities are listed in
`../NATIVE_INPUT_INDEX.json`.

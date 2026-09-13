# Frozen Controlled Contracts and Trajectories

This directory is the reviewer-facing mirror of the 18 controlled-benchmark
contract files that participate in the locked 114-file final-candidate
identity.  The files define the frozen grammar/families, method and baseline
budgets, oracle requirements, analytic geometry policy, event schema, model,
scene, resource semantics, and four named trajectories with hash-binding
sidecars.

## Contents

| Group | Files | Purpose |
|---|---:|---|
| Top-level contracts | 4 | Freeze the program family, budgets, oracle obligations, and asset identities |
| Model/semantics assets | 6 | Define the analytic model, scene, geometry policy, event schema, logical rules, and object/resource semantics |
| Trajectories and sidecars | 8 | Four trajectory records plus one content-binding sidecar for each |

`MIRROR_MANIFEST.json` records the digest of every file and its corresponding
candidate-relative path.  These records are byte-identical to the runtime copy
under:

```text
final-build/03_experiments/contracts/EXP-S4-002_CONTROLLED_UNSEEN_FORMAL_FREEZE/
```

The duplication is intentional and small: the final-build copy preserves the
exact runtime/identity layout, while this copy gives reviewers a direct
contracts-and-trajectories entry point.  The release validator checks both
copies.  Do not edit one copy independently.

## Boundary

These are controlled analytic benchmark assets.  They are not OpenArm URDFs,
robot meshes, calibrated physical measurements, or proof of real-world
clearance.  Their hashes establish input identity; correctness still depends
on the declared model and contract assumptions.

No general license is granted by this pre-release staging copy.  Final
distribution remains subject to author approval and repository-wide rights
review.

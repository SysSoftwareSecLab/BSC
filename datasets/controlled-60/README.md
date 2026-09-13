# Controlled-60

Controlled-60 is a frozen, purpose-built controlled cohort. Sixty deterministic sources were generated and admitted before evaluation; there was no replacement or resampling. The oracle split is 30 safe and 30 unsafe.

`cohort_manifest.jsonl` binds every program to its seed, structural family, generator hash, canonical-AST hash, time bound, and source SHA-256. `oracle_labels.jsonl` contains the independent-software oracle label without copying the historical records that carried private operator or host fields.

The oracle implementation was separate from the verifier and did not consume verifier outputs. It shared the project specification and controlled assets, so this is software-path independence, not an external blinded team.

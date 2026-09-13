# Final Mac cohort revalidation

This directory contains the compact, anonymous projection of `EXP-M5-MAC-FINAL-COHORT-REVALIDATION-20260913-R2`. All 165 frozen formal-source cases were executed through the final candidate build identity.

| Cohort | N | VWB | VIO | UNK | Frozen-verdict agreement |
|---|---:|---:|---:|---:|---:|
| Controlled-60 | 60 | 30 | 30 | 0 | 60/60 |
| External-40 | 40 | 32 | 8 | 0 | 40/40 |
| External-25 | 25 | 7 | 16 | 2 | 25/25 |
| Rare-40 | 40 | 8 | 32 | 0 | 40/40 |
| **Total** | **165** | **77** | **86** | **2** | **165/165** |

The two UNK outcomes are `CASE-5E9D045FADBAF9641867` and `CASE-C59370A58980CDCFF6DC`. Both have unsafe oracle labels, both returned `controlled-interval-conservative-unresolved`, and both produced `BLOCK`. Therefore exact binary-oracle agreement is 163/165 while agreement with the previously frozen corrected verdicts is 165/165.

Files:

- `controlled-60.jsonl`, `external-40.jsonl`, `external-25.jsonl`, and
  `rare-40.jsonl`: compact per-case outcomes with source hashes, labels,
  verdicts, release decisions, coverage, counts, runtime, and trace
  fingerprints. A trace attached to `VIO` is marked as a qualified violation
  counterexample; a trace attached to `UNK` is marked as unresolved diagnostic
  evidence and does not establish a violation;
- `summary.json`: the original path-free R2 aggregate;
- `scope_and_interpretation.json`: the evidence boundary and the distinction between binary-oracle agreement and frozen-verdict agreement;
- `method_imports.json`: the 19 modules actually imported by this entrypoint, with hashes, plus the locked candidate-root hash;
- `SHA256SUMS`: integrity hashes for this directory.

Runtime is reported as fresh descriptive measurement and is not treated as a verdict. These four cohorts use the frozen semantic/analytic adapter; native OpenArm/MoveIt/FCL revalidation is a separate evidence path.

# Frozen evaluation cohorts

This directory is a compact, reviewer-facing projection of the four formal-source cohorts used by the final Mac revalidation. It contains the scored source programs, anonymous manifests, and oracle labels. It does not contain internal work logs, duplicated archives, or machine-specific paths.

| Cohort | Scored | Oracle safe | Oracle unsafe | Intended role |
|---|---:|---:|---:|---|
| Controlled-60 | 60 | 30 | 30 | Frozen purpose-built controlled evaluation |
| External-40 | 40 | 32 | 8 | Development/regression evidence |
| External-25 | 25 | 7 | 18 | Existing evaluation/diagnostic cohort recomputed during the correction era |
| Rare-40 | 40 | 8 | 32 | Deterministic schedule-exposure challenge with safe controls |

These finite cohorts support benchmark-specific evidence. They are not population samples and are not used to claim population-level reliability. External-40 is explicitly a development/repair set. External-25 is not described as an untouched post-repair holdout.

Each cohort contains:

- `programs/`: every source program in the scored denominator;
- `cohort_manifest.jsonl`: one anonymous, artifact-relative identity record per scored source;
- `oracle_labels.jsonl`: one label record per scored source, including the
  oracle/method independence boundary and the verdict implied by a binary
  oracle label. That field is not a prediction that the verifier must return a
  binary verdict; a fail-closed `UNK` can therefore differ without releasing an
  unsafe program;
- `admission_funnel.json`: attempted, admitted, excluded, and scored counts where applicable;
- `SHA256SUMS`: integrity hashes for that cohort directory.

The corresponding final verifier outcomes are in `results/final-revalidation/cohorts/`.

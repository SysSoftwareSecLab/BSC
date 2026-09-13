# Baselines and matched controls

This directory contains only the baseline implementations and result slices used by the paper's final claims. Historical superseded runs and known incorrect intermediate summaries are excluded from the formal-result folders. Boundary statements and adverse outcomes remain explicit.

- `rq1-runtime-and-gpt/`: IndependentRuntime@100 and the GPT-5.5 semantic-diagnosis control on Controlled-60, External-40, and External-25, plus UntimedExternal outcomes.
- `ir-sampler/`: ordinary External-65 and Rare-40 rollout evidence, including nondetections and unresolved cases.
- `finite-budget/`: the 100-seed equal-checker-call comparison, including the exact-search budget exhaustion.
- `spin-translation-consistency/`: all 27 translated sequential H/R cases and the strict interpretation boundary.
- `rq2-ablations/`: all matched transition rows for the three mechanism-removal diagnostics.

Sampling non-detection is not a safety certificate. The SPIN study is a translation-consistency check on a simplified shared subset, not an end-to-end superiority comparison. The ablations are semantic-necessity diagnostics on pre-specified affected subsets, not population effect sizes.

## Anonymous source projection

Two convenience copies of pipeline source replace only the same-length
reviewer/authorization metadata literal used by the anonymous final-build
projection. Their public SHA-256 digests are disclosed in
`../final-build/PRIVACY_REDACTIONS.json`; unsalted pre-redaction digests are
withheld during double-anonymous review. No control flow, sampling budget,
oracle rule, or verdict computation was modified. Running those projected
pipelines may change emitted provenance text and its downstream digest.

Private version-control identifiers in selected dataset/baseline records were
also replaced by neutral freeze IDs while retaining source SHA-256 values.
`../results/final-revalidation/release-audit/PROVENANCE_PRIVACY_REDACTIONS.json`
records that release-only projection.
Unsalted digests for omitted historical IR inputs and the pre-redaction SPIN
runner are also withheld: because those private inputs are not shipped, the
digests add no reviewer-side byte check and could confirm a guessed private
artifact. Public files remain bound by the regenerated release manifests.
The SPIN translator under `spin-translation-consistency/source/` is a
historical source projection: its private Git lookup was deliberately
neutralized, so it is not the shipped rerun command. Reviewers can instead
verify all packaged Promela models, per-case results, source hashes, and counts
with `../results/final-revalidation/tools/recount_baselines.py`.

## Public recount status

Run the standard-library, read-only audit from the artifact root:

```bash
python3 results/final-revalidation/tools/recount_baselines.py
```

The audit recomputes the packaged row-level claims for RQ1 (Controlled-60,
External-40, and the 25 valid External-25 cases), all five budgets of the
ordinary IR-sampler cohort, all 27 SPIN rows and model/source hashes, all 90 RQ2
paired transitions, and all 2,121 finite checker-call rows. These components
pass their row-level checks. In particular, the Rare6 exact-search row at 1,024
checker calls is the retained `0/1` budget-exhausted outcome, while the matched
random method is `79/100`.

The finite-budget rows bind the included programs, seeds, budgets, counters,
and outcomes. An earlier staging draft also repeated the SHA-256 identity of an
omitted semantic `SearchEnvironment` object in all 2,121 rows. That digest was
not a machine fingerprint, but it did not identify reviewer-visible bytes and
was unnecessary for the published reconstruction; the public rows therefore
record `WITHHELD_NOT_PUBLICLY_BOUND` instead.

The audit reports the overall package as `PARTIAL`, not unqualified `PASS`,
because two compact slices are aggregate-only: Rare-40 omits its per-program
and rollout rows, and the finite-budget wall-clock summary omits its raw rows.
Their disclosed aggregates are arithmetic-checked but cannot be independently
rebuilt row by row from this package. See `PUBLIC_RECOUNT_STATUS.json` for the
machine-readable coverage statement.

Two further boundaries remain visible. The Controlled-60 GPT control completed
299/300 scheduled judgments and retained one timeout; it was not replaced or
silently counted as an error. The RQ2 recount verifies all 90 transition rows,
but 20 separated-state inputs are result-record-only because their source bytes
are omitted. The 60 controlled source identities remain byte-verifiable.

The ordinary IR-sampler records deliberately retain stale historical
`first_detection_rollout` and `paired_result_at_1000` values for the two
declared corrected cases. The recount uses the corrected `prefixes` and
`bisafecode_release_verdict` fields; it neither uses nor rewrites those legacy
fields. With these explicit boundaries, the baseline evidence supports
`READY-WITH-BOUNDARY` for E07.

The `COMPLETE_PENDING_COUNTER_AUDIT` value inside the frozen SPIN summary is a
historical run status. The current public recount of its 27 rows, models, and
packaged source identities passes; the historical field is preserved rather
than rewritten.

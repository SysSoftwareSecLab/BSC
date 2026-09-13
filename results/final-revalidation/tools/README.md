# Portable Audit Tools

`recount_public_results.py` uses only the Python standard library. It checks the
four released cohort result files against their program bytes, manifests,
oracle-label records, fail-closed decisions, trace qualifications, the
anonymous public-build identity, and
summary totals.

Run it from any location with:

```bash
python3 results/final-revalidation/tools/recount_public_results.py
```

This is a record recount, not a fresh verifier execution. No portable or native
campaign wrapper is distributed in this review artifact.

`recount_generated_cohorts.py` validates the no-repair and controlled-extension
generation/result projections. It cross-checks the public generation records
and packaged program bytes, recomputes the scored denominators and verdict
counts, preserves the Q4 target miss and monitor failure, and verifies the
correction-record joins:

```bash
python3 results/final-revalidation/tools/recount_generated_cohorts.py
```

`recount_baselines.py` is also standard-library and read-only. It validates the
packaged RQ1, IR-sampler, SPIN, RQ2-ablation, and finite-budget evidence against
the available row records, source/model bytes, and frozen summaries:

```bash
python3 results/final-revalidation/tools/recount_baselines.py
```

Its final status is intentionally `PARTIAL` when a component is represented
only by aggregate evidence. In this compact release, Rare-40 sampling and the
finite-budget wall-clock slice are aggregate-only; the remaining listed
baseline components are recomputed from their packaged rows. The script does
not execute a verifier or baseline and does not modify any result, manifest, or
checksum file.

`recount_native_results.py` validates the 30 accepted Table III slots, their
frozen schedule and public input bindings, the 15 C115 scheduled identities,
two controlled-program negatives, 15 family-matched native negative
repetitions, 20 dense aggregate chunks, the accepted source/build record,
zero-send evidence, and the historical H/R/C denominator:

```bash
python3 results/final-revalidation/tools/recount_native_results.py
```

It does not execute the verifier, native campaign, complete qualification, or
hardware experiment. Its dense check recounts retained aggregates; it does not
recompute 479,136 individual distances.

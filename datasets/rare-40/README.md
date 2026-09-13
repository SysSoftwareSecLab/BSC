# Rare-40

Rare-40 is a deterministic schedule-exposure challenge, not a prevalence sample. It contains 32 unsafe challenge programs across four exposure strata and eight safe controls.

The method-facing inputs contained opaque source identities but no role or exposure-stratum labels. The included sources were regenerated with the frozen generator and accepted toolchain, then checked byte-for-byte against all 40 frozen SHA-256 values. The generator identity is recorded in `admission_funnel.json` and every per-case manifest row.

Because this cohort is deliberately constructed to stress schedule exposure, its 8/32 safe/unsafe composition must not be interpreted as a real-world frequency estimate.

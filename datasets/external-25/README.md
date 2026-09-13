# External-25

External-25 is an existing evaluation/diagnostic cohort whose current outcomes include correction-era recomputation. It is not an untouched post-repair holdout.

The retained aggregate funnel is 36 attempted sources, 29 in the reported
admission denominator, and 25 oracle-valid scored sources. The scored set
contains 17 human and eight natural-LLM sources, with seven oracle-safe and 18
oracle-unsafe labels. Complete row-level records for the seven attempts outside
the 29 were not retained, so this artifact does not reconstruct their
individual reasons. Within the fully retained 12-attempt LLM slice, 10 passed
syntax and binding, one of those matched a prior canonical-AST identity, nine
remained correctness-eligible, one was oracle-invalid, and eight entered the
scored cohort. `admission_funnel.json` preserves this evidence boundary and the
four admitted-but-oracle-invalid identities; only the 25 scored source files
are included.

The 25 sources were recovered byte-for-byte from the recorded historical freeze
commit and checked against the frozen cohort manifest. They were not
reconstructed. The final outcomes are seven VWB, 16 VIO, and two UNK. Both UNK
cases are oracle-unsafe and were blocked; no oracle-unsafe program was released.
Their retained traces are unresolved diagnostics, not qualified violation
counterexamples.

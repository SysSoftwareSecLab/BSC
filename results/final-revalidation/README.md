# Final-Revalidation Evidence

This directory contains accepted final-build records for the four portable
cohorts, Table III, C115, and the generated-program studies. Public tools recount
the included cases, slots, frozen schedules, file bindings, and chunk-level
aggregates; they do not re-execute the portable or native campaigns.

- `cohorts/` contains 165 accepted portable program results.
- `table-iii/` contains the 30 accepted native formal slots (six workloads by
  five fresh measured runs).
- `c115/` contains 15 accepted final offline slot records, the unsafe controls,
  zero-send evidence, and the dense reference summaries.
- `generated-cohorts/` contains the compact no-repair and controlled-extension
  result records and their correction evidence.
- `tools/` contains the standard-library reviewer recount utilities.
- `release-audit/` contains packaging, privacy, provenance-redaction, and
  Ubuntu-integration records that support the release without cluttering the
  repository root.

The accepted dense reference report covers 479,136 individual distance rows.
The compact public check uses 20 retained aggregate chunks; it verifies their
accounting and recorded extrema rather than recomputing each distance.

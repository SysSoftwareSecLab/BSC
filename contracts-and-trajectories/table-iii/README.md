# Table III Frozen Inputs

`inputs/FORMAL_BENCHMARK_SCHEDULE.json` fixes the six workloads, five measured
repetitions, fresh-process rule, and no-replacement denominator. The directory
also includes the H/R source programs, C safe/unsafe sources and descriptors,
frozen trajectories, attachment table, and checked-pair table used by the
accepted native reference run.

The public files use anonymous artifact-relative paths and public URDF/SRDF
file identities. Trajectory points and the accepted experiment-level semantic
join identifiers are unchanged. `inputs/CASE_INPUT_BINDINGS.json` maps all six
cases to their public source and trajectory files, accepted loaded-registry
IDs, and source-referenced subsets. File-byte identities are indexed by
`../NATIVE_INPUT_INDEX.json`.

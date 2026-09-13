# Oracle and Qualification Evidence

This directory binds the separately implemented oracle sources and accepted
qualification records to the released final-build bytes. The implementations
share declared task semantics and model assets; they are not blinded annotations
or independent physical measurements.

The public checks validate selected record and file bindings. They check the
distance-comparator policy, its `1e-12 m` bound, and the recorded fail-closed
negative controls, but do not re-execute the complete qualification.

- `ORACLE_INDEX.json` identifies the portable oracle implementations.
- `native/` identifies the final native oracle and qualification modules.
- `qualification/` contains the accepted final qualification, the compact
  distance-error qualification, and the H/R observer summary.

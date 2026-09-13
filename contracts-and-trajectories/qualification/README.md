# Native Contract-Qualification Reference

`reference-contract/` is the reviewer-visible frozen reference used by the
accepted final qualification. It includes the geometry policy, logical rules,
model, scene, object/resource semantics, and four trajectory/sidecar pairs.

The accepted qualification record admits the candidate/reference comparison at
`1e-12 m` and records fail-closed outcomes for missing and zero-underbound
evidence; see `../../oracles/qualification/`. The public recount checks that
record and its policy binding; it does not rerun the qualification. The
`1e-12 m` value is a numerical candidate/reference error term, not calibrated
FCL accuracy or physical uncertainty.

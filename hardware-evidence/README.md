# Historical OpenArm Evidence

This directory contains the compact anonymous projection of the historical
OpenArm deployment campaign. The primary denominator is fixed at five trials
per family:

| Family | Controller tasks completed | Post-run evidence outcome |
|---|---:|---|
| Handover | 5/5 | 5 PASS |
| Resource | 5/5 | 5 PASS |
| Collision | 5/5 | 3 PASS, 2 coverage UNKNOWN |

All five collision controller tasks completed, and all five sampled-state
clearance checks passed. Between-sample evidence is PASS for C01--C03 and
UNKNOWN for C04/C05 because host-receive gaps of 41.547412 ms and 61.317265 ms
exceeded the frozen 25 ms coverage limit. These two UNKNOWN labels mean that
the post-run interval evidence was insufficient. They are not verifier
verdicts, observed collisions, or controller-task failures.

All five collision slots remain bound to the frozen P044 campaign input, whose
campaign-level VWB predates the campaign, and no retained non-VWB or blocked
send was found. The narrower per-slot verdict-to-release-to-send join was not
retained. Fresh per-slot verification or tokenized enforcement is therefore
not reconstructed from later records.

`HISTORICAL_DEPLOYMENT_SUMMARY.json` contains family totals.
`HISTORICAL_SLOT_INDEX.jsonl` contains the 15 primary slot outcomes.
`C_DISPATCH_BOUNDARY.json` records the supported campaign-level provenance and
the narrower per-slot trace boundary. `EQUIPMENT_SUMMARY.md` records the robot,
observer, and two compute hosts without serial or network identifiers.

The final-build C115 offline records are separate evidence under
`results/final-revalidation/c115/`. They confirm the current frozen software
path and do not replace or relabel historical primary slots.

Hardware media and giant raw traces are not included.
`R_MODEL_RELATIVE_CLEARANCE_BOUND.json` records the exact telemetry-derived,
residual-adjusted minimum of 0.007825518167755598 mm and its 0.00783 mm display
value. Because assembly and object-pose errors were not calibrated and no
independent sensor measurement was available, this value is not interpreted as
a physical-clearance certificate.

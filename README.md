# BSC: Anonymous Review Artifact

BSC performs bounded explicit-state verification of timed bimanual
robot programs written in a restricted Python subset. It checks concurrent
control flow, grasp state, object authority, shared resources, and continuous
carried-object geometry. Only a `verified-within-bounds` (`VWB`) verdict permits
dispatch; `VIO`, `UNK`, and `INV` fail closed to `BLOCK`.

This anonymous artifact connects the paper's claims to the released verifier,
frozen inputs, accepted final-revalidation records, oracle qualifications,
experimental environments, and compact reviewer-side checks. Start with
[`REPRODUCE.md`](REPRODUCE.md) for the supported commands.

## Results at a glance

| Evidence | Accepted result | Reviewer-side check |
|---|---|---|
| Four frozen cohorts | 165/165 frozen-verdict agreement; 163/165 binary-oracle agreement; every non-VWB outcome maps to `BLOCK` | Recounts all 165 released case records |
| Table III | 30/30 fixed slots across six workloads and five repetitions; 15 `VWB/RELEASE`, 15 `VIO/BLOCK`; zero `UNK`, timeout, runner error, or replacement | Recounts the 30 accepted slot records and five-run RSS medians |
| C115 final offline revalidation | 15/15 scheduled C/H/R identities passed; two controlled-program negatives and 15 family-matched native negative repetitions all produced `VIO/BLOCK` | Checks the released schedules, bindings, outcomes, and zero-send records |
| Dense native comparison | 1,932 states and 479,136 distance rows; zero reported negative distances | Recounts the 20 retained chunk aggregates |
| Historical OpenArm evidence | Handover 5/5 PASS; resource 5/5 PASS; collision 3 PASS + 2 coverage UNKNOWN | Checks the retained trial records and coverage thresholds |

A reviewer-side **recount** recomputes totals and consistency conditions from
the released rows or aggregates. It does not rerun the full portable, native,
LLM, or robot campaigns.

## Quick verification

Run from the artifact root:

```bash
# Linux
sha256sum --check MANIFEST.sha256

# macOS
shasum -a 256 --check MANIFEST.sha256

python3 final-build/tools/verify_candidate_projection.py --artifact-root .
python3 results/final-revalidation/tools/recount_public_results.py
python3 results/final-revalidation/tools/recount_generated_cohorts.py
python3 results/final-revalidation/tools/recount_baselines.py
python3 results/final-revalidation/tools/recount_native_results.py
```

These Python checks use only the standard library and do not contact a robot,
ROS graph, model provider, or network service. Expected outputs and the
separate Ubuntu native-source build procedure are documented in
[`REPRODUCE.md`](REPRODUCE.md).

## Repository contents

| Path | Contents |
|---|---|
| `final-build/` | Anonymous verifier projection, build identities, and published native-probe source |
| `datasets/` | Controlled-60, External-40, External-25, and Rare-40 programs and labels |
| `contracts-and-trajectories/` | Frozen portable and native contracts, trajectories, models, schedules, and policies |
| `oracles/` | Oracle identities and qualification records |
| `results/final-revalidation/` | Cohort, Table III, C115, dense, zero-send, generated-cohort, and release-audit records, plus recount tools |
| `hardware-evidence/` | Historical OpenArm H/R/C outcomes and the collision-evidence boundary |
| `llm-generation/` | Experiment-relevant prompts, task cards, raw outputs or failures, and admission decisions |
| `baselines/` | Runtime, finite-budget, ablation, GPT-control, and SPIN shared-subset evidence |
| `environment/` | Recorded Mac and Ubuntu software/hardware environments and native build guidance |
| `correction-ledger.md` | Corrections, final outcomes, and retained unfavorable cases |
| `REPRODUCE.md` | Supported integrity, recount, and native-build procedures |
| `LICENSE_STATUS.md` | Review-only access and third-party license notices |
| `MANIFEST.sha256` | SHA-256 identity of every released file except the manifest itself |

The detailed paper-claim map is available at
[`results/CLAIM_EVIDENCE_INDEX.md`](results/CLAIM_EVIDENCE_INDEX.md).

## Experimental scope

The four cohorts are finite benchmarks. External-40 served as a
development/regression set; External-25 and the controlled cohorts were
recomputed after the reported corrections. The released records retain both
External-25 unsafe-to-`UNK` outcomes, and both fail closed to `BLOCK`. The
artifact reports benchmark evidence without extending it to a population-level
reliability claim.

Table III records the accepted final native run: six workloads, five fresh
measured processes per workload, and the corrected five-run median RSS values.
All 30 slots bind their inputs, effective modules, policy, and accepted native
server. In the recorded Ubuntu environment, a clean Release build of the
published server source reproduced the accepted server binary byte-for-byte.
The public package recounts these accepted records; it does not distribute the
internal campaign orchestrator.

Historical deployment evidence is kept separate from BSC verifier
verdicts. Handover and resource trials passed 5/5. Collision trials produced
three post-run PASS outcomes and two coverage UNKNOWN outcomes because C04 and
C05 exceeded the frozen 25-ms host-receive-gap limit. The retained archive
shows no non-VWB or blocked candidate being sent and supports the executed
phases at campaign level; it does not contain a complete per-slot
`verdict -> release -> send` join. Final offline revalidation checks the frozen
programs, contracts, and model path without rewriting that historical record.

The minimum telemetry-derived, residual-adjusted, model-relative clearance is
0.00783 mm. Assembly and object-pose errors were not calibrated, and no
independent physical-clearance sensor was used; the value is not presented as
a physical-clearance certificate.

## Recorded platforms

| Role | Recorded platform |
|---|---|
| Portable cohort revalidation | Apple M1 Pro Mac, 8-core CPU, 16 GB RAM; macOS 15.6.1; CPython 3.9.6 |
| Native final revalidation | Intel Core i9-14900HX, 32 logical CPUs, 16,451,567,616 bytes RAM; Ubuntu 22.04.5; CPython 3.10.12; ROS 2 Humble; MoveIt 2.5.9; FCL 0.7.0 |
| Physical deployment | Dual-arm OpenArm configuration represented by the released model assets |
| Historical H/R observer | Apple iPhone 16 Pro Max rear main camera; original 1080 x 1920 video at 30 fps; categorical observation only |

Detailed records are under `environment/` and `hardware-evidence/`.
Machine-specific timing and RSS values apply to the recorded Ubuntu host.

## Review and anonymity

This is a selective, double-anonymous review artifact, not a full
project-history export. It includes the evidence needed to inspect the reported
claims while excluding private correspondence, Git history, personal paths,
account identifiers, redundant engineering traces, and unreviewed media.
Reported failures, `UNK` outcomes, corrections, and claim-relevant boundaries
remain visible. Project-authored material is supplied for peer review under
[`LICENSE_STATUS.md`](LICENSE_STATUS.md); a later open-source release is a
separate author decision.

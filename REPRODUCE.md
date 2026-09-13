# Reproducing and Auditing the BSC Evidence

This package supports integrity verification for every released byte, targeted
recounts of the paper-facing result records and fixed denominators, and an
Ubuntu build of the published native FCL probes. It does not distribute the
internal campaign wrappers, so these commands do not re-execute the accepted
portable or native campaigns. The frozen inputs, released verifier, native
source, qualifications, and accepted reference results remain available for
direct inspection.

No command in this document contacts robot hardware.

## 1. Verify the downloaded bytes

Enter the artifact root and verify the top-level manifest:

```bash
# Linux
sha256sum --check MANIFEST.sha256

# macOS
shasum -a 256 --check MANIFEST.sha256
```

Expected outcome: every listed file reports `OK`. `MANIFEST.sha256` is the only
file not listed inside itself. Do not repair a mismatch by regenerating hashes;
obtain a clean copy instead.

Verify the anonymous 114-file final-build projection:

```bash
python3 final-build/tools/verify_candidate_projection.py --artifact-root .
```

Expected result:

```text
PASS: anonymous 114-file projection root 28866e84438e8e72e46b5a8a0724587aaea2b6f46bbb37450c0860c38fd7d419
PASS: 109 byte-exact files + 5 disclosed privacy redactions
PASS: 19 imported modules are byte-exact and exclude all redacted paths
PASS: 2 published baseline duplicates match their redacted source modules
PASS: 18 controlled-contract mirror files
```

The five projected files differ only in identifying provenance or
documentation literals. `final-build/PRIVACY_REDACTIONS.json` lists the public
paths and public identities without disclosing removed strings or private-file
digests.

## 2. Run the full public recount

The four recount tools use only Python's standard library:

```bash
python3 results/final-revalidation/tools/recount_public_results.py
python3 results/final-revalidation/tools/recount_generated_cohorts.py
python3 results/final-revalidation/tools/recount_baselines.py
python3 results/final-revalidation/tools/recount_native_results.py
```

They read released per-case, per-slot, and compact chunk records. They do not
import ROS, load the native probe, call an LLM provider, or connect to hardware.

### 2.1 Frozen cohort expectations

| Cohort | Cases | VWB | VIO | UNK | Frozen-verdict agreement |
|---|---:|---:|---:|---:|---:|
| Controlled-60 | 60 | 30 | 30 | 0 | 60/60 |
| External-40 | 40 | 32 | 8 | 0 | 40/40 |
| External-25 | 25 | 7 | 16 | 2 | 25/25 |
| Rare-40 | 40 | 8 | 32 | 0 | 40/40 |
| **Total** | **165** | **77** | **86** | **2** | **165/165** |

Binary-oracle agreement is 163/165. The two differences are the retained
External-25 unsafe-to-UNK outcomes; both map to BLOCK.

### 2.2 Native reference-record expectations

`recount_native_results.py` verifies:

- exactly 30 Table III slots with six workloads and repetitions 1--5;
- 15 VWB/RELEASE and 15 VIO/BLOCK outcomes;
- zero Table III UNK, timeout, runner error, or replacement;
- source, actual structural-counter, effective-module, policy, accepted
  native-server, runtime, and RSS bindings, including the six five-run RSS
  medians;
- exactly 15 C115 scheduled identities, five each for C, H, and R, all PASS in
  final offline revalidation;
- two controlled-program unsafe cases and 15 native unsafe formal runs, all
  BLOCK in the zero-send path;
- an accepted dense reference report covering 1,932 states, 434,700 self rows,
  and 44,436 world rows, plus a public aggregate recount over 20 compact chunks;
- the released distance-comparator qualification record, including its public
  policy binding, `1e-12 m` bound, and recorded missing/underbound fail-closed
  dispositions; the recount does not rerun the qualifier; and
- historical H 5/5 PASS, R 5/5 PASS, C 3 PASS + 2 coverage UNKNOWN, including
  both retained gap measurements and the collision traceability boundary.

Expected final status:

```json
"status": "PASS"
```

For the dense lane, this `PASS` means that the 20 retained chunk-level records
recount to the accepted totals and reported extrema. It does not mean the public
script recalculated all 479,136 individual distances.

Table III's recorded summed primary wall time is 12,757.501275449 seconds. The
largest recorded combined Python/native peak RSS is 1,734,012,928 bytes.
Runtime and RSS are measurements from the recorded machine, not cross-machine
identity requirements.

## 3. Build the public native probes on Ubuntu

The accepted native environment was:

- Ubuntu 22.04.5 LTS, kernel 6.8.0-138-generic, x86_64;
- Intel Core i9-14900HX, 32 logical CPUs;
- 16,451,567,616 bytes RAM;
- Python 3.10.12 and GCC/G++ 11.4.0;
- ROS 2 Humble with rclpy 3.3.21;
- MoveIt 2.5.9, FCL 0.7.0, and glibc 2.35.

Install and build using `environment/ubuntu/BUILD_AND_INSTALL.md`. The retained
source/build receipt is `final-build/SOURCE_BUILD_BINDING.json`. In the recorded
environment, the clean Release server build had SHA-256:

```text
a82194d39f0223380bdabddda0f532ab3225e9aaf041072f1670b7a8ea4fce20
```

This exactly matched the persistent server binary used in all 30 accepted Table
III slots. Both native probes build from the published source; only the
persistent server has a published byte-for-byte binding to the accepted Table
III binary. A different compiler or linker environment may produce different
bytes; such a difference does not by itself establish a verdict difference.

The public native probe source can load the released URDF/SRDF and produce FCL
distance tables. The package intentionally does not provide the internal
campaign orchestrators needed to regenerate the full Table III and C115 result
trees. Do not treat a locally written substitute wrapper as the accepted
protocol unless it independently preserves the schedules, input bindings,
qualifications, timeouts, fail-closed rules, and zero-send boundary.

For a future full native rerun, provision 20 GiB of free workspace. The
accepted wrapper used a fail-closed 20 GiB preflight gate. Four GiB RAM covers
the largest recorded Table III process; 8 GiB is recommended for build and
runtime headroom. These are capacity guidelines, not measured requirements for
the compact recount.

## 4. What is and is not rerun here

| Capability | Public status | Interpretation |
|---|---|---|
| Manifest and final-build verification | Included and tested | Verifies released bytes and final-build projection |
| Portable, generated-cohort, baseline, and native record recounts | Included and tested | Recomputes published counts from released rows/chunks |
| Native probe source build | Included; validated in recorded Ubuntu environment | Rebuilds the native FCL executables |
| Portable 165-case execution wrapper | Not distributed | Released rows can be recounted; full verifier execution is not advertised as one-command reproducible |
| Table III/C115 campaign wrappers | Not distributed | Accepted reference evidence is complete; full native campaign re-execution is not advertised by this package |
| Historical robot experiment | Inspect only | Never repeated by an artifact command |

The omitted wrappers carried private provenance identities and path-bound
assumptions. Publishing an untested textual rewrite would create a stronger but
false reproducibility claim. The scientific outcomes are represented by
allowlisted compact evidence instead; the exact curation is recorded in
`results/final-revalidation/release-audit/UBUNTU_INTEGRATION_CHANGES.md`.

## 5. Zero-send boundary

The final offline revalidation records state:

- no ROS graph contact;
- no controller or CAN contact;
- no action client;
- no motion or gripper command;
- zero goal sends; and
- zero hardware trials.

These checks apply to the accepted offline runs represented in the package.
They do not turn an arbitrary reviewer-authored native wrapper into a safe
execution procedure. Run the released recount tools on any machine; build or
exercise native probes only in an isolated offline environment.

## 6. Reading the historical hardware records

The 15 historical primary trials are separate from the 15 final offline C115
identities. For the historical collision family, C04 and C05 remain post-run
coverage UNKNOWN because 41.547412 ms and 61.317265 ms exceed the 25 ms limit.
Supplementary checks do not replace those slots.

The historical records support campaign-level execution provenance and contain
no retained non-VWB or blocked-to-send record. They do not contain a complete
per-slot pre-dispatch verdict/release/send join. The later final-build offline
revalidation cannot create that missing historical join.

## 7. Interpreting discrepancies

1. A manifest or public identity mismatch is `FAIL`; stop and obtain a clean
   copy.
2. A missing qualification or input is `BLOCKED`; do not infer a verdict.
3. A verdict/count mismatch is `FAIL`; preserve the actual output.
4. A runtime-only difference is a new machine measurement, not automatically a
   scientific failure.
5. Apply only the released numeric comparator. Do not tune a tolerance after
   seeing a result.
6. Any unexpected controller, robot, CAN, or actuation contact invalidates an
   offline run immediately.

Write fresh outputs outside the released evidence tree, for example under
`reproduced/`. Never overwrite `results/final-revalidation/`.

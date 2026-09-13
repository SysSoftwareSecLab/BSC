# Hash and Identity Boundary

Hashes serve different roles in this artifact. Their field names and nearby
paths determine what can be recomputed from the public package.

## Public file-byte identities

Hashes in `MANIFEST.sha256`, directory `SHA256SUMS` files,
`NATIVE_INPUT_INDEX.json`, and fields named `*_file_sha256` identify bytes
included in this artifact. `source_sha256` also identifies an included source
file when the record provides its artifact-relative source path.

These values can be recomputed directly from the released files.

## Final-build root

`final-build/BUILD_MANIFEST.json` defines a canonical 114-file anonymous
projection with root:

```text
28866e84438e8e72e46b5a8a0724587aaea2b6f46bbb37450c0860c38fd7d419
```

Of those files, 109 are byte-exact and five contain disclosed identity-only
redactions. The 33-file final verifier core is byte-exact. The root identifies
the reviewer-visible build, not a Git commit or an undistributed private tree.

## Retained semantic join identifiers

Native Table III slots preserve `program_content_id` and
`trajectory_content_ids` under `retained_reference_semantic_ids`. These are
verifier-emitted experiment-level identifiers used to join the accepted run to
its frozen program and trajectory namespace. The reviewer-facing model files
use a sanitized public identity namespace, so these retained semantic values
are not presented as file digests recomputable from the public projection.

The trajectory JSON files use the singular field `trajectory_content_id` for
the same semantic namespace. The Table III `CASE_INPUT_BINDINGS.json` record
separately maps every case to its public source and
trajectory file SHA-256 values, its accepted loaded-registry IDs, and the
smaller subset of trajectory IDs referenced by that source. H/R loaded 16 files
that deduplicate to 14 semantic IDs; this registry count is not a claim that
each program executes 14 trajectories.

All public source, model, policy, module, and trajectory file bytes have their
own separate recomputable SHA-256 values.

## Accepted native server identity

`executed_probe_binary_sha256` identifies the persistent native server executed
by all 30 accepted Table III slots:

```text
a82194d39f0223380bdabddda0f532ab3225e9aaf041072f1670b7a8ea4fce20
```

The compiled binary is not redistributed. `final-build/SOURCE_BUILD_BINDING.json`
records the three included native-source files and the retained clean Release
build result. The included server source, SHA-256
`8a149032b12f190d3ce1d96669d873dd17468fc1f60d7e77f140f753decc5fbc`,
reproduced the accepted binary byte-for-byte in the recorded Ubuntu environment.
This establishes the source-to-binary binding for that toolchain, not a claim
of bit-identical output from arbitrary compilers or linkers.

## Scientific guard constants

The byte-exact verifier source contains 21 historical dependency hashes used by
input, freeze, and fail-closed guards. Some guarded payloads are outside this
selective release. The constants remain because changing them would change the
accepted executable source. Their presence does not claim that omitted bytes
are publicly available.

Hashes establish integrity and provenance. They do not by themselves establish
correctness, independence, licensing, physical truth, or safety.

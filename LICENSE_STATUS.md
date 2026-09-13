# Licensing Status

This is a double-anonymous peer-review artifact. No project-wide open-source,
data, or media license is granted for the project-authored materials. Access is
provided so reviewers can inspect and verify the evidence reported in the
paper. No general reuse, redistribution, modification, or commercial-use grant
should be inferred.

The included OpenArm description and MoveIt-configuration materials retain
their Apache License 2.0 notices under
`contracts-and-trajectories/external-retrieval/`. Those third-party materials
remain governed by their original terms and are not relicensed by this
repository. The selected scope is the generated URDF/SRDF and the 11 collision
meshes required by the reviewer-visible native model. Visual meshes and
unneeded package files are excluded.

Compiled probes, captured shared libraries, private archives, and media are not
included. Package versions and principal sonames are provided for system
dependencies instead of copied binaries.

Release manifests and result records publish SHA-256 values for included bytes
and documented scientific identities. Two narrow exceptions are explicit:

1. the byte-exact verifier source retains 21 historical scientific dependency
   guard constants because deleting them would change the accepted source and
   its fail-closed checks; and
2. the accepted Table III server-binary SHA-256 is retained because all 30
   formal slots executed that binary and the included source reproduced it
   byte-for-byte in the recorded Ubuntu environment.

Neither exception implies that an omitted dependency or binary is licensed or
redistributed. Hashes establish identity or provenance; they do not establish
authorship, copyright permission, physical truth, or safety.

A later public or open-source release, if any, is a separate author decision.

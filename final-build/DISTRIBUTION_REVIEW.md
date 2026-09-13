# Distribution Review Status

## Included payload

The original candidate consists of 96 UTF-8 Python files and 18 UTF-8 JSON
files.  The anonymous public projection contains 109 byte-exact files and five
same-length, privacy-redacted files.  Across those files, four replacements
affect reviewer/authorization labels, one affects a private
authorization-source description, and one affects a private
documentation-filename literal. The public digests are retained in
`BUILD_MANIFEST.json`. The pre-redaction digests of those five privacy-edited
files and the private tree root are not part of this selective reviewer
disclosure; the removed identity values are not repeated or cryptographically
exposed in the release.

The byte-exact verifier source also retains 21 historical SHA-256 constants
used by scientific input and freeze guards. They cover prior verifier modules,
model and scene assemblies, controlled-run adapters, selection metadata, and
generation/oracle roots. The guarded dependency bytes are not distributed and
the constants do not establish their public reproducibility; retaining the
constants preserves the accepted source bytes and fail-closed guard semantics.
They are scientific dependency pins, not author, account, host, or Git
identifiers.

Automated checks found no binary file, symlink, Git metadata, personal absolute
path, account identifier, hostname, email address, credential-like assignment,
or targeted author-name occurrence in the public projection.  Two occurrences
of the word `token` are internal opaque-case identifiers, not authentication
secrets.

The Python files import only the Python standard library and other
`bisafecode` modules directly. Native OpenArm/MoveIt/FCL source builds require
the separately installed packages recorded under `../environment/ubuntu/`.
The selected OpenArm model assets and their Apache-2.0 notices are included
under `../contracts-and-trajectories/c115/model/`; compiled system libraries are
not bundled.

The four authorization-metadata pipeline modules were absent from the 19-module
Mac R2 actual-import manifest.  The documentation-reference module was absent
as well.  This is a narrow execution boundary, not a claim that the original
and public source trees have the same root digest.  Executing a projected
pipeline can change emitted provenance text and its hash; no verdict logic was
modified.

## Current release decisions

- Project-generated source, contracts, trajectories, data, and prompts are a
  review-only disclosure; no project-wide open-source license is granted.
- Only the selected OpenArm collision assets covered by the retained
  Apache-2.0 notices are redistributed.
- Compiled binaries, system libraries, images, audio, and video are excluded.
- The complete combined tree receives a final identity, syntax, and anonymity
  scan before it is copied to the review medium.

The present directory makes no claim that omitted dependencies can be
redistributed and does not silently place third-party material under a project
license.

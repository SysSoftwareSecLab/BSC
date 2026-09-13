# Third-party retrieval and identity

## OpenArm description assets

- Project: `enactic/openarm_description`
- Package version recorded locally: `1.0.0`
- Upstream repository: `https://github.com/enactic/openarm_description`
- License: Apache License 2.0
- Included license evidence: `openarm_description_LICENSE.txt`
  (`sha256:c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`)
- Included scope: the generated bimanual URDF, generated SRDF, two end-effector
  collision meshes, eight arm collision meshes, and one body collision mesh
  referenced by the native evidence.
- Excluded scope: visual meshes and other files that were not among the 205
  frozen references.

The generated SRDF also depends on the bimanual MoveIt configuration distributed
with `enactic/openarm_ros2`; the locally recorded package version is `0.3.0` and
its upstream repository is `https://github.com/enactic/openarm_ros2`. Its
license is Apache-2.0. Its included license evidence is
`openarm_ros2_LICENSE.txt`
(`sha256:cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`).

The local source directories were copied without Git metadata, so an exact
upstream commit is not asserted. Package versions document the source context.
The per-file SHA-256 values in `../NATIVE_INPUT_INDEX.json` identify the exact
released URDF, SRDF, collision meshes, license notices, and native inputs. A
future retrieval must match those hashes; a matching version label alone is not
sufficient.

## Native and system libraries

Captured ELF executables and shared libraries are intentionally omitted. Use
the package/version information in `environment/ubuntu/` to recreate the
runtime. Consult each installed Debian/Ubuntu package's copyright file under
`/usr/share/doc/<package>/copyright`; this export does not guess SPDX identifiers
for transitive runtime libraries and does not redistribute their binary bytes.

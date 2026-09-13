# Ubuntu Native-Revalidation Environment

| Field | Recorded value |
|---|---|
| CPU | Intel Core i9-14900HX |
| Logical CPUs | 32 |
| Installed RAM | 16,451,567,616 bytes |
| Architecture | x86_64 |
| OS | Ubuntu 22.04.5 LTS |
| Kernel | 6.8.0-138-generic |
| Python | CPython 3.10.12 |
| Compiler | GCC/G++ 11.4.0 |
| ROS | ROS 2 Humble; rclpy 3.3.21 |
| MoveIt | 2.5.9 |
| FCL | 0.7.0 |
| glibc | 2.35 |

`ENVIRONMENT.json` is the machine-readable platform record. `packages.lock`
lists the accepted binary-package versions. `NATIVE_DEPENDENCIES.json` records
principal sonames without redistributing compiled libraries.
`PYTHON_IMPORTS.json` binds the native lanes to the included final-build module
bytes. `../../final-build/SOURCE_BUILD_BINDING.json` records the clean Release
source/build result and its byte-for-byte match to the accepted persistent
Table III server binary.

The Table III reference run consumed 12,757.501275449 seconds of summed primary
wall time. Its largest recorded combined Python/native peak RSS was
1,734,012,928 bytes. Those values describe this host.

Serial numbers, UUIDs, hostnames, network addresses, account identifiers,
absolute paths, captured binaries, and shared-library payloads are not
published.

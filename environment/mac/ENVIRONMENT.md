# Mac Portable-Revalidation Environment

The 165-case portable final-build revalidation used the following Mac host:

| Field | Recorded value |
|---|---|
| Chip | Apple M1 Pro |
| CPU cores | 8 (6 performance, 2 efficiency) |
| Installed memory | 16 GB |
| Architecture | arm64 |
| Operating system | macOS 15.6.1 |
| Darwin release/build | 24.6.0 / 24G90 |
| Python | CPython 3.9.6 |
| Compiler reported by Python | Clang 17.0.0 (`clang-1700.0.13.5`) |
| ROS / MoveIt / FCL | Not used by this portable lane |
| Robot or controller contact | None |

The chip, core count, and installed memory are recorded separately in
`hardware_inventory.json`. The accepted revalidation record
(`environment.json`) captured the OS, architecture, Python, and compiler fields
but did not embed the CPU brand string. Keeping the inventory separate preserves
the accepted run record byte-for-byte while supplying the requested hardware
description.

Serial number, hardware UUID, provisioning identifier, hostname, network
address, personal paths, and kernel build-user text are not released.

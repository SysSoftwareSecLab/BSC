# Equipment Summary

This inventory identifies the experiment-relevant equipment at a level useful
for technical review. Unique device identifiers are intentionally omitted.

| Role | Equipment | Publicly relevant configuration |
|---|---|---|
| Robot platform | OpenArm v1.0 bimanual platform | Two seven-joint arms on a shared body, with left and right parallel grippers; the released URDF, SRDF, and collision meshes define the model used by the native checks |
| Portable-revalidation computer | Apple M1 Pro Mac | 8 CPU cores (6 performance, 2 efficiency), 16 GB RAM, arm64, macOS 15.6.1, CPython 3.9.6 |
| Native-revalidation computer | Intel Core i9-14900HX Ubuntu host | 32 logical CPUs, 16,451,567,616 bytes RAM, x86_64, Ubuntu 22.04.5, CPython 3.10.12, ROS 2 Humble, MoveIt 2.5.9, FCL 0.7.0 |
| Categorical H/R observer | Apple iPhone 16 Pro Max rear main camera | Original 1080 x 1920 video at 30 fps; fixed within a family block and used for visible event, retention, and occupancy adjudication |

The phone was not used as metric instrumentation. The retained protocol did
not qualify pixel-to-millimetre measurement, force sensing, or hidden-surface
collision sensing. Metric/model-relative bounds came from frozen physical
marks or objects, controller telemetry, and the declared error budget.

The Mac and Ubuntu environment records are under `environment/mac/` and
`environment/ubuntu/`. The released native model and its third-party license
notices are under `contracts-and-trajectories/c115/model/` and
`contracts-and-trajectories/external-retrieval/`.

Serial numbers, hardware UUIDs, provisioning identifiers, hostnames, network
addresses, account identifiers, room/location information, and institutional
identifiers are not published.

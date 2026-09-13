# Build the Native Probe Sources

These commands build the two included OpenArm/MoveIt/FCL probe executables.
They are a source-build check, not the complete Table III or C115 campaign
rerun; the internal campaign wrappers are not distributed.

The ROS 2 Humble apt repository must already be configured. On Ubuntu 22.04:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends \
  build-essential cmake \
  python3.10 python3.10-venv python3-pip \
  python3-numpy python3-psutil python3-yaml \
  libassimp-dev libccd-dev libeigen3-dev libfcl-dev liboctomap-dev \
  ros-humble-moveit-core ros-humble-rclpy
```

From the artifact root:

```bash
source /opt/ros/humble/setup.bash
mkdir -p reproduced
cmake -S final-build/native-probe-source \
  -B reproduced/native-probe-build \
  -DCMAKE_BUILD_TYPE=Release
cmake --build reproduced/native-probe-build --parallel
```

The retained machine-readable receipt is
`../../final-build/SOURCE_BUILD_BINDING.json`. In the recorded Ubuntu
environment, configure and build exited 0 in 1.64 and 5.54 seconds. The clean
Release server reproduced the accepted Table III server binary byte-for-byte:

```text
a82194d39f0223380bdabddda0f532ab3225e9aaf041072f1670b7a8ea4fce20
```

This establishes the source-to-binary binding for the recorded toolchain. It
does not assert bit-identical output from arbitrary compilers or linkers.

A future full native campaign rerun should provision at least 20 GiB of free
disk because the accepted wrapper enforced a fail-closed 20 GiB preflight gate.
Four GiB RAM covers the largest recorded Table III process; 8 GiB is
recommended for build and runtime headroom. The compact recount requires none
of this native environment.

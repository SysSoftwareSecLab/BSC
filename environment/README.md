# Environment Records

`mac/` records the Apple M1 Pro host used for the 165-case portable final-build
revalidation. That lane used neither ROS nor robot hardware.

`ubuntu/` records the Intel Core i9-14900HX host and the Ubuntu, Python,
compiler, ROS, MoveIt, FCL, glibc, package, soname, and public Python-module
identities used by the accepted Table III and C115 native revalidations.

Timing and RSS values belong to the recorded Ubuntu machine. They are
descriptive measurements, not cross-machine identity requirements. Captured
system libraries and virtual environments are excluded; package versions and
principal sonames are supplied instead.

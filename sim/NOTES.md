# Simulation environment notes (N1)

## Stack
- Windows 11 + WSL2, Ubuntu 22.04 (jammy)
- ROS 2 Humble, Gazebo Classic 11, TurtleBot3 burger (built from source)
- Headless only: gzserver without gzclient

## Modifications to the turtlebot3 packages
These files live outside this repository
(`~/tb3_ws/src/turtlebot3_simulations/`) and cannot be symlinked.
Re-apply when rebuilding the environment from scratch.

### LiDAR publish rate: 5 -> 20 Hz
- `turtlebot3_gazebo/models/turtlebot3_burger/model.sdf`
- `<update_rate>5</update_rate>` -> `<update_rate>20</update_rate>`
- Rationale: match the 20 Hz control loop (Nav2 controller_server default)
- Measured: /scan at 19.95 Hz, sigma = 0.4 ms, n = 2063 over 100 s
- The sensor uses `type="ray"` (CPU raycast), so GPU failure does not
  affect scan data.

### Package override
The source build of `turtlebot3_gazebo` overrides the apt package in
`/opt/ros/humble`. This is intended: the 20 Hz change lives in the
source tree. colcon warns about it on every build; the warning is
expected. If the workspace overlay is not sourced after
`/opt/ros/humble/setup.bash`, the apt version wins and the LiDAR
silently drops back to 5 Hz.

## Files managed here via symlink
- `worlds/a1_course.world` -> `sim/worlds/a1_course.world`
- `launch/a1_headless.launch.py` -> `sim/launch/a1_headless.launch.py`

Verify after any rebuild:

    grep -c 'model name="obs' \
      ~/tb3_ws/install/turtlebot3_gazebo/share/turtlebot3_gazebo/worlds/a1_course.world

Expected: 5. A 0 means the symlink did not propagate to `install/`,
and edits to the world file will silently have no effect.

## Known constraints
- Gazebo GUI unavailable under WSL2 (D3D12 device removal).
  Headless is preferred anyway: the C3 overnight sweep must run
  unattended.
- RTF stays at 1.00; SimTime - RealTime holds a constant ~0.27 s offset
  with no cumulative drift.
- `/clock` is published at 10 Hz, so simulation timestamps are quantized
  to 0.1 s (the CSV shows each timestamp twice). The controller itself
  runs at a correct 20 Hz, so this does not affect A1. It does prevent
  microsecond-level measurement of the A/B/C/D timing chain in stages
  B and C -- a separate wall clock or a higher `/clock` rate is needed.

## Thresholds
| value  | number | basis |
|--------|--------|-------|
| d_col  | 0.15 m | confirmed: range min 0.12 m + 3-sigma noise 0.03 m |
| d_stop | 0.20 m | confirmed: d_col 0.15 + 0.033 lag distance, rounded up |
| d_slow | 0.40 m | confirmed: SLOW engages and the robot reaches d_stop without collision across 7 runs (6-12); min_range settles at 0.262-0.277 m, well clear of d_stop 0.20 |
The LDS-01 sits 3.2 cm behind the chassis centre. All three values are
sensor-referenced, and the A2 firmware uses the same reference.

## Course validation (runs 10-12)
Obstacle lateral clearance L = 0.42 m from the path. With a +/-48 deg
sector and obstacle radius 0.15 m, R = L/sin(48) - r predicts 0.415 m,
but the measured minimum is 0.262-0.277 m: the robot passes closer than
the straight-line model during turns. The geometric model tracks the
direction of change correctly (L 0.38 -> 0.42 moved min 0.200 -> 0.270,
predicted +0.054, measured +0.070), so it is usable for planning but not
for absolute values.

At L = 0.38 the minimum ranged 0.1997-0.2224 across four runs, straddling
d_stop 0.20, and STOP fired in one run out of four. At L = 0.42 the spread
narrowed to 0.0144 m and STOP is 0 in all three runs.

Pass criteria met: 3/3 complete in under 60 s (48.7-49.0 s), zero
collisions, step counts within 1.3% (774-784), SLOW engaged in every run.

## Forward sector
Originally +/-15 deg (indices 0-15 and 345-359). With a lateral
clearance L, an obstacle of radius r reads R = L/sin(theta) - r. At
theta = 15 deg there is no L that both triggers SLOW and avoids
collision, so the sector was widened to +/-48 deg
(`range(0,48) + range(n-48,n)`). This reads the plan's "16 forward
min_range sectors" as 16 sectors of 6 rays each, which also matches
the 16 x 2 = 32 byte payload.

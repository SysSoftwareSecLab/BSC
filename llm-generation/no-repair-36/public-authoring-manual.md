# Blind external-validity public authoring manual

Status: `FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE`

This is the complete language manual supplied equally to the natural-LLM
author and the independent human authors. It describes the programming task;
it contains no expected verdict, oracle label, BiSafeCode output, controlled
benchmark program or target defect.

## Required source shape

- Submit one UTF-8 Python source string/file.
- Define exactly one parameterless `task()` entry function.
- Optional parameterless helper functions are allowed only as the two branches
  of `parallel(left_lane, right_lane)`.
- Use 1--16 source-level API calls.
- Do not import modules, assign variables, call attributes, use I/O, use
  recursion, or define dynamic loops.
- `mode` and `ready` are the only finite inputs. Their domains are
  `mode in {"fast", "safe"}` and `ready in {False, True}`.
- An `if` must have an `else`. Control nesting depth is at most two.
- A loop must be `for <name> in range(2)`, `range(3)`, or `range(4)`.
- At most one two-lane `parallel` region may be active; nested parallelism is
  prohibited.
- A barrier appears only at the top level of a parallel helper. Both helpers
  must contain the same barrier identifiers in the same order.
- The complete structural time upper bound must not exceed 5 seconds.

## Fixed identifiers

- Arms: `"left"`, `"right"`
- Objects: `"payload_alpha"`, `"payload_beta"`
- Shared resources: `"fixture_alpha"`, `"tool_beta"`
- Timed literal durations: `20000000`, `40000000`, `80000000` nanoseconds

Trajectory names below are documentation labels only. Source code must paste
the corresponding exact content hash.

| Documentation label | Arm | Exact trajectory content hash | Duration |
|---|---|---|---:|
| `left_approach` | left | `d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26` | 400 ms |
| `left_retreat` | left | `952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6` | 400 ms |
| `right_approach` | right | `3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609` | 400 ms |
| `right_retreat` | right | `1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea` | 400 ms |

## Available API

```python
wait(arm, duration_ns)
move(arm, trajectory_content_hash)
close(arm, object_id, duration_ns)
open(arm, object_id, duration_ns)
acquire(arm, resource_id)
release(arm, resource_id)
transfer_authority(object_id, sender, receiver)
barrier(barrier_id)
parallel(left_helper, right_helper)
```

Operational meaning:

- `wait` occupies the named arm for the duration.
- `move` executes the exact named arm trajectory and takes 400 ms.
- `close` completes a grasp after its duration when the object state permits.
- `open` releases an object from the arm after its duration when the arm holds
  that object.
- `acquire` claims a currently available shared resource.
- `release` gives up a resource currently owned by that arm.
- `transfer_authority` changes object authority between arms during a
  stationary dual grasp in the shared handover zone.
- `parallel` starts the two named helper functions concurrently.
- Matching `barrier` calls form a rendezvous: both lanes wait until they reach
  the same barrier and their active operations have finished.

The system later considers all four `mode`/`ready` valuations and all permitted
concurrent schedules. As an author, implement the assigned task naturally. You
are not asked to classify the program, target a failure, or predict any
experimental result.

## Neutral syntax skeletons

Sequential skeleton:

```python
def task():
    # Replace this comment with fixed-API calls or a permitted if/loop.
    pass
```

Parallel skeleton:

```python
def left_lane():
    # Fixed-API calls for the left lane.
    barrier("rendezvous")

def right_lane():
    # Fixed-API calls for the right lane.
    barrier("rendezvous")

def task():
    parallel(left_lane, right_lane)
```

The skeletons demonstrate syntax only; they are not benchmark programs and do
not carry a safety label.

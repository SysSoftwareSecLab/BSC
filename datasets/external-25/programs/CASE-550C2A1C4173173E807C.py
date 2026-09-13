def left_lane():
    acquire("left", "tool_beta")
    wait("left", 40000000)
    barrier("acquired")
    wait("left", 40000000)
    barrier("release_phase")
    release("left", "tool_beta")

def right_lane():
    wait("right", 40000000)
    barrier("acquired")
    acquire("right", "tool_beta")
    wait("right", 40000000)
    barrier("release_phase")
    release("right", "tool_beta")

def task():
    parallel(left_lane, right_lane)

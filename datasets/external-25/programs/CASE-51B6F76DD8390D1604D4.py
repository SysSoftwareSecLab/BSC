def left_lane():
    acquire("left", "tool_beta")
    wait("left", 40000000)
    release("left", "tool_beta")
    barrier("sync_point")

def right_lane():
    acquire("right", "tool_beta")
    wait("right", 40000000)
    release("right", "tool_beta")
    barrier("sync_point")

def task():
    parallel(left_lane, right_lane)
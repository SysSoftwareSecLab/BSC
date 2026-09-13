def left_lane():
    acquire("left", "tool_beta")
    barrier("owner_ready")
    barrier("competition_complete")
    release("left", "tool_beta")

def right_lane():
    barrier("owner_ready")
    acquire("right", "tool_beta")
    barrier("competition_complete")

def task():
    parallel(left_lane, right_lane)

# LOCKED_RAW_PENDING_BLIND_EVALUATION
def left_lane():
    acquire("left", "tool_beta")
    wait("left", 80000000)
    barrier("ctl_sync_alpha")
    release("left", "tool_beta")

def right_lane():
    wait("right", 20000000)
    acquire("right", "tool_beta")
    barrier("ctl_sync_alpha")
    release("right", "tool_beta")

def task():
    parallel(left_lane, right_lane)
    if ready:
        wait("left", 40000000)
    else:
        wait("right", 80000000)

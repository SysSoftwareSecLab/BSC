# LOCKED_RAW_PENDING_BLIND_EVALUATION
def left_lane():
    acquire("left", "fixture_alpha")
    wait("left", 40000000)
    barrier("ctl_sync_alpha")
    release("left", "fixture_alpha")

def right_lane():
    wait("right", 40000000)
    acquire("right", "fixture_alpha")
    barrier("ctl_sync_alpha")
    release("right", "fixture_alpha")

def task():
    parallel(left_lane, right_lane)
    if ready:
        wait("left", 40000000)
    else:
        wait("right", 40000000)

# LOCKED_RAW_PENDING_BLIND_EVALUATION
def left_lane():
    wait("left", 80000000)
    barrier("ctl_sync_alpha")
    wait("left", 20000000)

def right_lane():
    wait("right", 20000000)
    barrier("ctl_sync_alpha")
    wait("right", 40000000)

def task():
    close("left", "payload_alpha", 40000000)
    for cycle in range(3):
        wait("left", 80000000)
    parallel(left_lane, right_lane)
    open("left", "payload_alpha", 20000000)

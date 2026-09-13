# LOCKED_RAW_PENDING_BLIND_EVALUATION
def left_lane():
    wait("left", 20000000)
    barrier("ctl_sync_alpha")
    wait("left", 40000000)

def right_lane():
    wait("right", 40000000)
    barrier("ctl_sync_alpha")
    wait("right", 80000000)

def task():
    close("right", "payload_beta", 80000000)
    for cycle in range(4):
        wait("right", 20000000)
    parallel(left_lane, right_lane)
    open("right", "payload_beta", 40000000)

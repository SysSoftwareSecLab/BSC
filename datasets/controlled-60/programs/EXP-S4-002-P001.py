# LOCKED_RAW_PENDING_BLIND_EVALUATION
def left_lane():
    close("left", "payload_beta", 20000000)
    barrier("ctl_sync_alpha")
    wait("left", 20000000)

def right_lane():
    wait("right", 20000000)
    close("right", "payload_beta", 20000000)
    barrier("ctl_sync_alpha")
    wait("right", 20000000)

def task():
    parallel(left_lane, right_lane)
    if ready:
        transfer_authority("payload_beta", "left", "right")
    else:
        transfer_authority("payload_beta", "right", "left")
    open("left", "payload_beta", 20000000)

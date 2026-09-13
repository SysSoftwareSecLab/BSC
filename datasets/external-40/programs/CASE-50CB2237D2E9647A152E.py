def left_lane():
    wait("left", 20000000)
    barrier("prep")

def right_lane():
    wait("right", 20000000)
    barrier("prep")

def task():
    parallel(left_lane, right_lane)

    # Main thread handover of payload_beta
    close("left", "payload_beta", 20000000)
    close("right", "payload_beta", 20000000)
    transfer_authority("payload_beta", "left", "right")
    open("right", "payload_beta", 20000000)
def left_lane():
    close("left", "payload_alpha", 20000000)
    wait("left", 40000000)
    open("left", "payload_alpha", 20000000)

def right_lane():
    close("right", "payload_beta", 20000000)
    wait("right", 40000000)
    open("right", "payload_beta", 20000000)

def task():
    parallel(left_lane, right_lane)

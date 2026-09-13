def left_lane():
    wait("left", 20000000)
    barrier("rendezvous")

def right_lane():
    wait("right", 20000000)
    barrier("rendezvous")

def task():
    parallel(left_lane, right_lane)
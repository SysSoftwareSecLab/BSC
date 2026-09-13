def left_lane():
    wait("left", 40000000)
    barrier("rendezvous")


def right_lane():
    wait("right", 40000000)
    barrier("rendezvous")


def task():
    parallel(left_lane, right_lane)

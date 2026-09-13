def left_lane():
    wait("left", 40000000)
    barrier("preparation_done")
    wait("left", 80000000)
    barrier("work_done")

def right_lane():
    wait("right", 20000000)
    barrier("preparation_done")
    wait("right", 80000000)
    barrier("work_done")

def task():
    parallel(left_lane, right_lane)

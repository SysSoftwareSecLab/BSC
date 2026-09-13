def left_lane():
    acquire("left", "tool_beta")
    barrier("claimed")
    wait("left", 40000000)
    barrier("done")
    release("left", "tool_beta")

def right_lane():
    barrier("claimed")
    acquire("right", "tool_beta")
    barrier("done")

def task():
    parallel(left_lane, right_lane)

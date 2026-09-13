def left_lane():
    acquire("left", "tool_beta")
    barrier("tool_competition")
    release("left", "tool_beta")

def right_lane():
    acquire("right", "tool_beta")
    barrier("tool_competition")
    release("right", "tool_beta")

def task():
    parallel(left_lane, right_lane)

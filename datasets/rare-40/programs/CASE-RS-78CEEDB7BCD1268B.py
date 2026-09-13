# EXP-S4-009_LOCKED_SOURCE
def left_lane():
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")

def right_lane():
    acquire("right", "tool_beta")
    release("right", "tool_beta")
    acquire("right", "tool_beta")
    release("right", "tool_beta")
    acquire("right", "tool_beta")
    release("right", "tool_beta")
    acquire("right", "tool_beta")
    release("right", "tool_beta")
    acquire("right", "fixture_alpha")
    release("right", "fixture_alpha")
    acquire("right", "tool_beta")
    release("right", "tool_beta")
    acquire("right", "tool_beta")
    release("right", "tool_beta")

def task():
    parallel(left_lane, right_lane)

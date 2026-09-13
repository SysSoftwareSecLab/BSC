# EXP-S4-009_LOCKED_SOURCE
def left_lane():
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")
    acquire("left", "tool_beta")
    release("left", "tool_beta")

def right_lane():
    acquire("right", "fixture_alpha")
    release("right", "fixture_alpha")

def task():
    parallel(left_lane, right_lane)

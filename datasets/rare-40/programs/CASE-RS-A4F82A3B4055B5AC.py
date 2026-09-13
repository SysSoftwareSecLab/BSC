# EXP-S4-009_LOCKED_SOURCE
def left_lane():
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")
    acquire("left", "tool_beta")
    release("left", "tool_beta")
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")
    acquire("left", "fixture_alpha")
    release("left", "fixture_alpha")

def right_lane():
    acquire("right", "tool_beta")
    release("right", "tool_beta")

def task():
    parallel(left_lane, right_lane)

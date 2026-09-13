def left_lane():
    acquire("left", "fixture_alpha")
    wait("left", 20000000)
    release("left", "fixture_alpha")
    barrier("turn")

def right_lane():
    wait("right", 20000000)
    barrier("turn")
    acquire("right", "fixture_alpha")
    wait("right", 20000000)
    release("right", "fixture_alpha")

def task():
    parallel(left_lane, right_lane)
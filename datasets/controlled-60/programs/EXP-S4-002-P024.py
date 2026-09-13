# LOCKED_RAW_PENDING_BLIND_EVALUATION
def task():
    if mode == "safe":
        acquire("right", "fixture_alpha")
        wait("right", 40000000)
        release("right", "fixture_alpha")
    else:
        wait("left", 20000000)
        acquire("left", "fixture_alpha")
        wait("left", 80000000)
        release("left", "fixture_alpha")
    for cycle in range(4):
        wait("right", 20000000)

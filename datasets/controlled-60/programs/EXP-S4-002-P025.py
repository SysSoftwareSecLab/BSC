# LOCKED_RAW_PENDING_BLIND_EVALUATION
def task():
    if mode == "safe":
        acquire("left", "fixture_alpha")
        wait("left", 80000000)
        release("left", "fixture_alpha")
    else:
        wait("right", 20000000)
        acquire("right", "fixture_alpha")
        wait("right", 80000000)
        release("right", "fixture_alpha")
    for cycle in range(3):
        wait("left", 20000000)

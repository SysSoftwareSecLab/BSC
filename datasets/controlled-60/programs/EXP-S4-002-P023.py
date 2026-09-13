# LOCKED_RAW_PENDING_BLIND_EVALUATION
def task():
    if mode == "safe":
        acquire("left", "tool_beta")
        wait("left", 40000000)
        release("left", "tool_beta")
    else:
        wait("right", 20000000)
        acquire("right", "tool_beta")
        wait("right", 20000000)
        release("right", "tool_beta")
    for cycle in range(4):
        wait("left", 20000000)

# LOCKED_RAW_PENDING_BLIND_EVALUATION
def task():
    if mode == "safe":
        acquire("right", "tool_beta")
        wait("right", 40000000)
        release("right", "tool_beta")
    else:
        wait("left", 40000000)
        acquire("left", "tool_beta")
        wait("left", 40000000)
        release("left", "tool_beta")
    for cycle in range(2):
        wait("right", 40000000)

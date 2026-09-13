def task():
    acquire("left", "tool_beta")
    wait("left", 40000000)
    release("left", "tool_beta")
    acquire("right", "tool_beta")
    wait("right", 40000000)
    release("right", "tool_beta")

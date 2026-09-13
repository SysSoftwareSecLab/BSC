def task():
    acquire("right", "tool_beta")
    close("right", "payload_beta", 20000000)
    wait("right", 40000000)
    open("right", "payload_beta", 20000000)
    release("right", "tool_beta")

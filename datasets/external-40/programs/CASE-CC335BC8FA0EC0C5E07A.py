def task():
    # Left arm handles payload_alpha
    close("left", "payload_alpha", 20000000)
    wait("left", 20000000)
    open("left", "payload_alpha", 20000000)

    # Right arm handles payload_beta
    close("right", "payload_beta", 20000000)
    wait("right", 20000000)
    open("right", "payload_beta", 20000000)

    # Use tool_beta with left arm
    acquire("left", "tool_beta")
    wait("left", 40000000)
    release("left", "tool_beta")
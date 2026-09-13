def task():
    if ready:
        acquire("left", "fixture_alpha")
        wait("left", 40000000)
        release("left", "fixture_alpha")
    else:
        acquire("right", "tool_beta")
        wait("right", 40000000)
        release("right", "tool_beta")
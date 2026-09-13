def task():
    if ready:
        acquire("left", "fixture_alpha")
        wait("left", 20000000)
        release("left", "fixture_alpha")
    else:
        acquire("right", "fixture_alpha")
        wait("right", 20000000)
        release("right", "fixture_alpha")
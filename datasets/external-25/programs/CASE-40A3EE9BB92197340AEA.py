def task():
    if ready:
        acquire("left", "fixture_alpha")
        wait("left", 40000000)
        release("right", "fixture_alpha")
    else:
        acquire("right", "fixture_alpha")
        wait("right", 40000000)
        release("right", "fixture_alpha")

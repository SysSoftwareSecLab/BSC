def task():
    acquire("left", "fixture_alpha")
    wait("left", 40000000)
    release("left", "fixture_alpha")

    acquire("right", "fixture_alpha")
    wait("right", 40000000)
    release("right", "fixture_alpha")
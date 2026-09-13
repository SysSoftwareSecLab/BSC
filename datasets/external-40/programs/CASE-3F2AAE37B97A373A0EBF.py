def task():
    acquire("left", "fixture_alpha")
    for cycle in range(3):
        wait("left", 40000000)
    release("left", "fixture_alpha")

def task():
    acquire("left", "fixture_alpha")
    for _ in range(3):
        wait("left", 40000000)
    release("left", "fixture_alpha")
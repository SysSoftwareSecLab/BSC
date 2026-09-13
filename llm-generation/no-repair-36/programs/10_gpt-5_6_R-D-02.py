def task():
    acquire("left", "fixture_alpha")
    if ready:
        release("right", "fixture_alpha")
    else:
        wait("right", 20000000)
    move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
def task():
    if mode == "fast":
        wait("left", 20000000)
    else:
        for step in range(2):
            wait("left", 40000000)

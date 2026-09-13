def left_lane():
    move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
    barrier("retreat_done")

def right_lane():
    move("right", "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea")
    barrier("retreat_done")

def task():
    close("left", "payload_alpha", 40000000)
    parallel(left_lane, right_lane)
    open("left", "payload_alpha", 40000000)

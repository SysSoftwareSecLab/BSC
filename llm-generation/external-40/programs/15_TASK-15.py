def left_lane():
    move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    close("left", "payload_alpha", 40000000)
    move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
    barrier("carry_complete")

def right_lane():
    wait("right", 80000000)
    barrier("carry_complete")

def task():
    parallel(left_lane, right_lane)

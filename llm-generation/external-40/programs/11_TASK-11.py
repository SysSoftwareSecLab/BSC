def left_lane():
    move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    barrier("rendezvous")
    barrier("rendezvous")
    transfer_authority("payload_alpha", "left", "right")
    barrier("rendezvous")
    open("left", "payload_alpha", 20000000)
    barrier("rendezvous")
    move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")

def right_lane():
    move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
    barrier("rendezvous")
    close("right", "payload_alpha", 20000000)
    barrier("rendezvous")
    barrier("rendezvous")
    barrier("rendezvous")
    move("right", "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea")

def task():
    parallel(left_lane, right_lane)

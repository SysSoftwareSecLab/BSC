def task():
    if mode == "fast":
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        close("left", "payload_alpha", 20000000)
        wait("left", 20000000)
        open("left", "payload_alpha", 20000000)
    else:
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        close("right", "payload_beta", 20000000)
        wait("right", 20000000)
        open("right", "payload_beta", 20000000)
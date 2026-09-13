def task():
    if mode == "fast":
        acquire("left", "fixture_alpha")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        close("left", "payload_alpha", 20000000)
        release("left", "fixture_alpha")
        acquire("right", "fixture_alpha")
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        close("right", "payload_beta", 20000000)
        release("right", "fixture_alpha")
    else:
        acquire("right", "fixture_alpha")
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        close("right", "payload_beta", 40000000)
        release("right", "fixture_alpha")
        acquire("left", "fixture_alpha")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        close("left", "payload_alpha", 40000000)
        release("left", "fixture_alpha")

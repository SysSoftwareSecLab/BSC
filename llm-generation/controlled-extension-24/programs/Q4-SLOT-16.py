def task():
    if ready:
        acquire("left", "tool_beta")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        close("left", "payload_alpha", 40000000)
        wait("left", 20000000)
        open("left", "payload_alpha", 40000000)
        release("left", "tool_beta")
    else:
        acquire("right", "tool_beta")
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        close("right", "payload_beta", 40000000)
        wait("right", 20000000)
        open("right", "payload_beta", 40000000)
        release("right", "tool_beta")
    for i in range(3):
        wait("left", 20000000)
        wait("right", 20000000)

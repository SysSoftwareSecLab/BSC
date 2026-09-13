def task():
    if ready:
        acquire("left", "tool_beta")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        release("left", "tool_beta")
        acquire("right", "tool_beta")
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        release("right", "tool_beta")
    else:
        wait("left", 80000000)
        wait("right", 80000000)
        acquire("left", "tool_beta")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        release("left", "tool_beta")
        acquire("right", "tool_beta")
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        release("right", "tool_beta")

def task():
    move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    close("left", "payload_beta", 40000000)
    move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
    transfer_authority("payload_beta", "left", "right")
    open("left", "payload_beta", 40000000)
    move("right", "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea")
def task():
    if ready:
        acquire("right", "fixture_alpha")
        move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
        close("right", "payload_beta", 40000000)
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        close("left", "payload_beta", 40000000)
        transfer_authority("payload_beta", "right", "left")
        open("right", "payload_beta", 40000000)
        move("right", "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea")
        move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
        release("right", "fixture_alpha")
    else:
        acquire("right", "fixture_alpha")
        acquire("left", "tool_beta")
        wait("right", 80000000)
        wait("left", 80000000)
        release("right", "fixture_alpha")
        release("left", "tool_beta")

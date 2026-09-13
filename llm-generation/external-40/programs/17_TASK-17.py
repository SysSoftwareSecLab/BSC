def task():
    acquire("right", "tool_beta")
    move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
    close("right", "payload_beta", 80000000)
    wait("right", 40000000)
    open("right", "payload_beta", 80000000)
    move("right", "1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea")
    release("right", "tool_beta")

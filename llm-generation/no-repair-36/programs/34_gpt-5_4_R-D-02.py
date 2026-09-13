def task():
    if ready:
        acquire("left", "fixture_alpha")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
        close("left", "payload_alpha", 40000000)
        move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
        release("left", "fixture_alpha")
    else:
        acquire("left", "fixture_alpha")
        wait("right", 20000000)
        release("right", "fixture_alpha")

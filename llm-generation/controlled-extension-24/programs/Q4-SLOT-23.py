def task():
    acquire("left", "fixture_alpha")
    move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    close("left", "payload_alpha", 40000000)
    for i in range(2):
        wait("left", 20000000)
        move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
        move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    if mode == "fast":
        move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
        open("left", "payload_alpha", 20000000)
        wait("left", 20000000)
    else:
        wait("left", 80000000)
        move("left", "952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6")
        open("left", "payload_alpha", 40000000)
    release("left", "fixture_alpha")

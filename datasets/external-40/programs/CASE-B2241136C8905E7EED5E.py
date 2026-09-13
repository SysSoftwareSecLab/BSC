def task():
    move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    close("left", "payload_beta", 20000000)
    for _ in range(3):
        wait("left", 40000000)
    open("left", "payload_beta", 20000000)
def task():
    if ready:
        close("left", "payload_beta", 20000000)
        close("right", "payload_beta", 20000000)
        transfer_authority("payload_beta", "left", "right")
    else:
        close("left", "payload_beta", 20000000)
        wait("left", 20000000)
        open("left", "payload_beta", 20000000)
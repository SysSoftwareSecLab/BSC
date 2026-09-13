def task():
    if mode == "fast":
        close("left", "payload_alpha", 20000000)
        wait("right", 20000000)
        close("right", "payload_alpha", 20000000)
        transfer_authority("payload_alpha", "left", "right")
        open("left", "payload_alpha", 20000000)
    else:
        close("right", "payload_beta", 20000000)
        wait("left", 20000000)
        close("left", "payload_beta", 20000000)
        transfer_authority("payload_beta", "right", "left")
        open("right", "payload_beta", 20000000)
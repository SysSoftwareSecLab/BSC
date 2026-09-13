def task():
    close("right", "payload_beta", 20000000)
    if mode == "fast":
        open("right", "payload_beta", 20000000)
        close("left", "payload_beta", 20000000)
        transfer_authority("payload_beta", "right", "left")
    else:
        wait("left", 40000000)
        close("left", "payload_beta", 20000000)
        wait("right", 40000000)
        transfer_authority("payload_beta", "right", "left")
        open("right", "payload_beta", 20000000)

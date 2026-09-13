def task():
    close("left", "payload_alpha", 20000000)
    for step in range(2):
        wait("left", 20000000)
    transfer_authority("payload_alpha", "left", "right")   # 缺陷：接收方未抓取
    close("right", "payload_alpha", 20000000)
    open("left", "payload_alpha", 20000000)
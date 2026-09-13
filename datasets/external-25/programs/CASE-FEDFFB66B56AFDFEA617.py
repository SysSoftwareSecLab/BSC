def task():
    close("left", "payload_alpha", 20000000)
    close("right", "payload_alpha", 20000000)
    open("left", "payload_alpha", 20000000)          # 发送方过早释放（缺陷）
    transfer_authority("payload_alpha", "left", "right")
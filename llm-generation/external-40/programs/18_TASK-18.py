def left_lane():
    barrier("dual_grasp")
    transfer_authority("payload_alpha", "left", "right")
    barrier("authority_transferred")
    open("left", "payload_alpha", 20000000)
    barrier("left_released")


def right_lane():
    close("right", "payload_alpha", 20000000)
    barrier("dual_grasp")
    barrier("authority_transferred")
    barrier("left_released")


def task():
    acquire("left", "fixture_alpha")
    close("left", "payload_alpha", 20000000)
    parallel(left_lane, right_lane)
    release("left", "fixture_alpha")

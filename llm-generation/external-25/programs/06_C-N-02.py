def left_lane():
    move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
    barrier("regroup")
    wait("left", 40000000)

def right_lane():
    move("right", "3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609")
    barrier("regroup")
    wait("right", 40000000)

def task():
    parallel(left_lane, right_lane)

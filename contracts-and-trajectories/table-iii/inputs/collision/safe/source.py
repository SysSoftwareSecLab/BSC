def left_branch():
    move("left", "54c56ee6ba95989616f0c6290de6885d5124c0ba25dfe560a392506e86fdea2b")

def right_branch():
    move("right", "a0a851d2e440802ffaa00fd40bd2043d27ca6c07fa8f6a51a9152f13c911a76d")

def task():
    parallel(left_branch, right_branch)

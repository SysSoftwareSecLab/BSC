def left_branch():
    move("left", "6214567b08f99b7c8337e2d76f3e3839fc5a9ffac68eb3c80748b4237a598d92")

def right_branch():
    move("right", "8542047265b7e94fd520ddcba9ef087739ac3e6cfd9444862fbd52c01954cafe")

def task():
    parallel(left_branch, right_branch)

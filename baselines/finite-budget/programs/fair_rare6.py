# EXP-S4-028 frozen schedule-sensitive workload
# all-true Boolean gates=6
def left_lane():
    acquire("left", "shared_fixture")
    wait("left", 2)
    release("left", "shared_fixture")
def right_lane():
    if rare_gate_0:
        if rare_gate_1:
            if rare_gate_2:
                if rare_gate_3:
                    if rare_gate_4:
                        if rare_gate_5:
                            acquire("right", "shared_fixture")
                            wait("right", 1)
                            release("right", "shared_fixture")
                        else:
                            wait("right", 1)
                    else:
                        wait("right", 1)
                else:
                    wait("right", 1)
            else:
                wait("right", 1)
        else:
            wait("right", 1)
    else:
        wait("right", 1)
def task():
    parallel(left_lane, right_lane)

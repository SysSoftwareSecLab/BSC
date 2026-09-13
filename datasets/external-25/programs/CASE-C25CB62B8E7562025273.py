def task():
    if mode == "fast":
        acquire("left", "fixture_alpha")
        release("right", "fixture_alpha")   # 错误所有者释放（缺陷）
    else:
        acquire("left", "fixture_alpha")
        release("left", "fixture_alpha")
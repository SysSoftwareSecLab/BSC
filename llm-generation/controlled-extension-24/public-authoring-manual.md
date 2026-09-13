# Blind external-validity public authoring manual — human extension R2

Status: `FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE`

本手册是 AUTHOR-D 与 AUTHOR-E 共同使用的完整语言说明。它只说明如何编写
程序，不包含期望 verdict、oracle 标签、BiSafeCode 输出、已有测试程序或目标
缺陷。请不要把手册中的单行语法片段拼成提交程序；请根据自己的任务卡独立设计。

## 1. 程序基本形状

- 每张任务卡提交一个 UTF-8 Python 文件；
- 正好定义一个不带参数的 `task()` 入口函数；
- 只有在使用 `parallel(left_lane, right_lane)` 时，才可以另外定义两个不带
  参数的通道函数；
- 整个文件使用1–16个 API 调用；
- 禁止 `import`、变量赋值、对象属性调用、输入输出、递归和动态循环；
- 只允许两个有限输入：`mode` 和 `ready`；
- `mode` 的取值是 `"fast"` 或 `"safe"`，`ready` 是 `True` 或 `False`；
- `if` 必须同时写 `else`，控制结构最多嵌套两层；
- 循环只能写成 `for i in range(2)`、`range(3)` 或 `range(4)`；
- 最多使用一个双通道 parallel，禁止 parallel 嵌套；
- 完整程序的结构时间上界不得超过5秒。

## 2. 固定标识符

- 机械臂：`"left"`、`"right"`
- 物体：`"payload_alpha"`、`"payload_beta"`
- 共享资源：`"fixture_alpha"`、`"tool_beta"`
- 可用时长：`20000000`、`40000000`、`80000000` 纳秒

## 3. 轨迹：代码必须粘贴完整哈希

`left_approach` 等词只是帮助人阅读的说明名称，绝不能作为 `move` 参数。

| 阅读名称 | 对应手臂 | 必须粘贴到代码中的完整哈希 | 时长 |
|---|---|---|---:|
| left_approach | left | `d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26` | 400 ms |
| left_retreat | left | `952b79dbc0b450154d6910502682bcb1bb62373808550d35da80c313aba41db6` | 400 ms |
| right_approach | right | `3e6910a45a223961ab95f4f5e8a63ac43f95c8270bb698234abaf687963ab609` | 400 ms |
| right_retreat | right | `1d58d45f58c932f071ca9641505e7a0a5abb03f7ed0d96b9aad5a93383fd32ea` | 400 ms |

下面只演示一个 API 调用的拼写，不是完整程序：

```python
move("left", "d2061067f4b66316e861e216e433941870034c1ef0d57baad7f533a3b9d5ca26")
```

错误写法：`move("left", "left_approach")`。

## 4. 可用 API

```python
wait(arm, duration_ns)
move(arm, trajectory_content_hash)
close(arm, object_id, duration_ns)
open(arm, object_id, duration_ns)
acquire(arm, resource_id)
release(arm, resource_id)
transfer_authority(object_id, sender, receiver)
barrier(barrier_id)
parallel(left_helper, right_helper)
```

- `wait`：指定手臂等待给定时长；
- `move`：执行与该手臂匹配的冻结轨迹，持续400 ms；
- `close`：经过给定时长后尝试抓取物体；
- `open`：经过给定时长后尝试释放物体；
- `acquire`：尝试取得共享资源；
- `release`：释放该手臂当前拥有的共享资源；
- `transfer_authority`：在双臂静止共同抓取且处于交接区域时转移物体控制权；
- `parallel`：同时启动左、右两个通道；
- 相同 `barrier` 形成会合点，两条通道都到达后才继续。

## 5. parallel 的三条硬语法规则

1. 左通道函数只能调用操作 `"left"` 的 API，右通道只能操作 `"right"`；
2. `barrier` 必须直接位于通道函数顶层，两条通道的 barrier 名称和顺序完全一致；
3. `transfer_authority` 只能写在主线程 `task()` 中，不能写在任一通道函数内。

允许的中性结构骨架如下；注释处需由作者自己设计，不能直接把骨架当提交程序：

```python
def left_lane():
    # 这里只能放 left 的操作
    barrier("sync")

def right_lane():
    # 这里只能放 right 的操作
    barrier("sync")

def task():
    parallel(left_lane, right_lane)
    # 如任务需要，transfer_authority 只能出现在这里
```

## 6. 条件与循环

条件必须是以下形式之一，并且必须有 `else`：

```python
if ready:
    # 自己设计
else:
    # 自己设计
```

```python
if mode == "fast":
    # 自己设计
else:
    # 自己设计
```

循环次数必须是字面量2、3或4。不要使用 `while`，也不要计算循环次数。

## 7. 提交原则

系统以后会考虑所有 `mode`/`ready` 取值和允许的并发调度。你的任务只是自然地
实现任务卡，不要尝试猜测验证器、制造特定错误或保证某种安全结论。正式提交前
可以自己检查和修改；一次性正式提交后不再更改。


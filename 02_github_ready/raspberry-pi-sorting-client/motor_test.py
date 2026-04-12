# motor_test.py
import time
# 修正：导入正确的类名 StepperMotor
from stepper_driver import StepperMotor


def main():
    # 根据需要调整初始频率
    # 修正：实例化正确的类名 StepperMotor
    conveyor = StepperMotor(frequency=800)

    print("=== 传送带测试程序 ===")
    print("指令：")
    print("  f  - 正转 (Forward)")
    print("  r  - 反转 (Reverse)")
    print("  s  - 停止 (Stop)")
    print("  +  - 提高速度 (频率 +100 Hz)")
    print("  -  - 降低速度 (频率 -100 Hz)")
    print("  q  - 退出程序 (Quit)")
    print("======================")

    try:
        while True:
            cmd = input("请输入指令 [f/r/s/+/-/q] : ").strip().lower()

            if cmd == "f":
                conveyor.forward()
            elif cmd == "r":
                conveyor.reverse()
            elif cmd == "s":
                conveyor.stop()
            elif cmd == "+":
                new_freq = conveyor.frequency + 100
                conveyor.set_speed(new_freq)
                # set_speed 内部已经有打印输出
            elif cmd == "-":
                # 确保频率不会低于 100 Hz
                new_freq = max(100, conveyor.frequency - 100)
                conveyor.set_speed(new_freq)
                # set_speed 内部已经有打印输出
            elif cmd == "q":
                print("退出程序...")
                break
            else:
                print("无效指令，请重新输入。")

    except KeyboardInterrupt:
        print("\n检测到 Ctrl+C，中止测试。")
    finally:
        # 确保在退出前停止电机并清理 GPIO 资源
        conveyor.stop()
        conveyor.cleanup()


if __name__ == "__main__":
    main()
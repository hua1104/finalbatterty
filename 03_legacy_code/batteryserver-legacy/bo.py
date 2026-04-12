import RPi.GPIO as GPIO
import time

class SorterMotor:
    def __init__(self, pul=31, dir=33, en=29, steps=400, speed=0.002): 
        # 注意：这里我把 speed 默认设为了 0.002，防止启动过快导致堵转
        self.PIN_PUL = pul
        self.PIN_DIR = dir
        self.PIN_EN  = en
        self.PUSH_STEPS = steps
        self.SPEED_DELAY = speed

        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)
        GPIO.setup(self.PIN_PUL, GPIO.OUT)
        GPIO.setup(self.PIN_DIR, GPIO.OUT)
        GPIO.setup(self.PIN_EN, GPIO.OUT)

        # 激活电机
        GPIO.output(self.PIN_EN, GPIO.LOW)
        GPIO.output(self.PIN_PUL, GPIO.LOW)
        print(f"[硬件] 拨片初始化完毕 | PUL:{pul}, DIR:{dir}, EN:{en} | 脉冲间隔:{speed}s")

    def _move(self, steps, direction):
        GPIO.output(self.PIN_DIR, GPIO.HIGH if direction == 1 else GPIO.LOW)
        for _ in range(steps):
            GPIO.output(self.PIN_PUL, GPIO.HIGH)
            time.sleep(self.SPEED_DELAY)
            GPIO.output(self.PIN_PUL, GPIO.LOW)
            time.sleep(self.SPEED_DELAY)

    def eject(self, angle=90):
        steps_to_move = int((angle / 90.0) * self.PUSH_STEPS)
        print(f" ---> 正在推出 {angle} 度 ({steps_to_move} 步)...")
        self._move(steps_to_move, direction=1)
        time.sleep(0.5)
        print(f" ---> 正在缩回...")
        self._move(steps_to_move, direction=0)

if __name__ == "__main__":
    print("====== 拨片电机独立诊断程序 ======")
    print("提示：如果电机发出'嗡嗡'声但不转，请拔掉驱动器上的 EN 线！\n")
    
    try:
        motor = SorterMotor(pul=31, dir=33, en=29, steps=400, speed=0.002)
        
        print("\n 倒计时 3 秒后开始 [轻微剔除测试 (45度)]...")
        time.sleep(3)
        motor.eject(angle=45)
        
        print("\n 倒计时 2 秒后开始 [完全剔除测试 (90度)]...")
        time.sleep(2)
        motor.eject(angle=90)
        
        print("\n 所有测试脉冲已发送完毕！")
        
    except KeyboardInterrupt:
        print("\n 测试被用户手动中断。")
    finally:
        print(" 正在清理 GPIO 资源...")
        GPIO.cleanup()
import RPi.GPIO as GPIO
import time

class SorterMotor:
    """
    分拣推杆电机控制类 (TB6600 + 42步进电机)
    """

    def __init__(self, pul=31, dir=33, en=29, steps=400, speed=0.0005):
        """
        初始化分拣电机
        :param pul: 脉冲引脚 (默认 Board 31)
        :param dir: 方向引脚 (默认 Board 33)
        :param en:  使能引脚 (默认 Board 29)
        :param steps: 推出的步数 (默认 400步，配合 1600细分约转 90度)
        :param speed: 脉冲间隔时间 (越小越快，默认 0.0005)
        """
        self.PIN_PUL = pul
        self.PIN_DIR = dir
        self.PIN_EN  = en
        self.PUSH_STEPS = steps
        self.SPEED_DELAY = speed

        # --- GPIO 初始化 ---
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)
        
        GPIO.setup(self.PIN_PUL, GPIO.OUT)
        GPIO.setup(self.PIN_DIR, GPIO.OUT)
        GPIO.setup(self.PIN_EN, GPIO.OUT)
        
        # 初始状态：启用电机 (EN=LOW)，脉冲置低
        GPIO.output(self.PIN_EN, GPIO.LOW)
        GPIO.output(self.PIN_PUL, GPIO.LOW)
        
        print(f"[推杆] 初始化完成 (PUL={self.PIN_PUL}, Steps={self.PUSH_STEPS})")

    def _move(self, steps, direction):
        """
        内部底层移动函数
        :param direction: 1 (推出), 0 (缩回)
        """
        # 设置方向 (如果方向反了，修改这里的 HIGH/LOW)
        GPIO.output(self.PIN_DIR, GPIO.HIGH if direction == 1 else GPIO.LOW)
        
        for _ in range(steps):
            GPIO.output(self.PIN_PUL, GPIO.HIGH)
            time.sleep(self.SPEED_DELAY)
            GPIO.output(self.PIN_PUL, GPIO.LOW)
            time.sleep(self.SPEED_DELAY)

    def push(self):
        """单独执行：推出"""
        self._move(self.PUSH_STEPS, direction=1)

    def retract(self):
        """单独执行：缩回"""
        self._move(self.PUSH_STEPS, direction=0)

    def eject(self, angle=90):
        """
        执行完整剔除动作：根据传入角度计算步数 -> 推出 -> 停顿 -> 缩回
        :param angle: 想要拨动的角度 (例如 40 或 75)
        """
        # 计算比例：(目标角度 / 90度) * 90度对应的总步数
        steps_to_move = int((angle / 90.0) * self.PUSH_STEPS)
        
        # print(f" [分拣] 目标角度: {angle}°, 计算步数: {steps_to_move}")
        
        # 1. 推出
        self._move(steps_to_move, direction=1)
        
        # 2. 停顿确保稳定
        time.sleep(0.2) 
        
        # 3. 缩回复位
        self._move(steps_to_move, direction=0)

    def disable(self):
        """
        释放电机 (脱机模式)
        长时间不工作时调用，可防止电机发热，但电机此时没有锁紧力。
        """
        GPIO.output(self.PIN_EN, GPIO.HIGH)
        
    def enable(self):
        """锁定电机 (恢复供电)"""
        GPIO.output(self.PIN_EN, GPIO.LOW)

    def cleanup(self):
        """清理资源"""
        self.disable()
        # 注意：这里不调用 GPIO.cleanup()，以免影响主程序里的其他传感器
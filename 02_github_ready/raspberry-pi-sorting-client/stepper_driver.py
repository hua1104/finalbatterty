import RPi.GPIO as GPIO
import time

class StepperMotor:
    """
    传送带步进电机驱动 (手动脉冲版 - 适配多目标追踪逻辑)
    """

    # --- GPIO 引脚配置 (和你刚才发的一致) ---
    PULSE_PIN = 18  # PUL
    DIR_PIN = 15    # DIR
    EN_PIN = 16     # EN

    # 逻辑电平
    ENABLE_LEVEL = GPIO.LOW
    DISABLE_LEVEL = GPIO.HIGH
    DIR_FORWARD = GPIO.LOW

    def __init__(self, frequency=None, direction_logic=None):
        # 注意：frequency 参数在这里没用，只是为了兼容主程序初始化时不报错
        self._setup_gpio()

    def _setup_gpio(self):
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)

        GPIO.setup(self.PULSE_PIN, GPIO.OUT)
        GPIO.setup(self.DIR_PIN, GPIO.OUT)
        GPIO.setup(self.EN_PIN, GPIO.OUT)

        # 默认初始化状态
        GPIO.output(self.EN_PIN, self.DISABLE_LEVEL) # 先脱机
        GPIO.output(self.PULSE_PIN, GPIO.LOW)
        GPIO.output(self.DIR_PIN, self.DIR_FORWARD)

    def enable(self):
        GPIO.output(self.EN_PIN, self.ENABLE_LEVEL)

    def disable(self):
        GPIO.output(self.EN_PIN, self.DISABLE_LEVEL)

    # ==========================================
    #  核心修复：添加 move_steps 方法
    # ==========================================
    
    def step(self, delay=0.0008):
        """手动走一步"""
        GPIO.output(self.PULSE_PIN, GPIO.HIGH)
        time.sleep(delay) 
        GPIO.output(self.PULSE_PIN, GPIO.LOW)
        time.sleep(delay)

    def move_steps(self, steps, delay=0.0008):
        """
        连续走 N 步 (阻塞式)
        main.py 中的 'move_steps' 调用的就是这里！
        """
        self.enable() # 确保电机有力矩
        GPIO.output(self.DIR_PIN, self.DIR_FORWARD) # 确保方向正确
        
        # 循环发脉冲
        for _ in range(int(steps)):
            self.step(delay)
            
        # 注意：这里不 disable，保持锁定，防止传送带打滑
        # 如果需要省电，可以在这里调用 self.disable()

    def cleanup(self):
        self.disable()
        GPIO.cleanup()
        print("[系统] 电机资源已释放")
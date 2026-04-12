# 文件名: ir_sensor.py
import RPi.GPIO as GPIO

class IRSensor:
    """
    红外避障/对射传感器封装类
    适配接线：
    - 传感器 B OUT -> 树莓派物理引脚 37 (BOARD)
    """
    
    # --- 关键修改：设置为物理引脚 37 ---
    SENSOR_PIN = 37   
    
    # 触发电平配置
    # 大多数红外模块检测到物体时输出低电平 (0V/LOW)
    # 如果您的传感器逻辑相反（检测到物体输出高电平），请改为 GPIO.HIGH
    TRIGGER_LEVEL = GPIO.LOW 

    def __init__(self):
        """初始化传感器"""
        # 使用 BOARD 物理引脚编号模式 (与您的电机驱动库保持一致)
        GPIO.setmode(GPIO.BOARD)
        
        # 设置引脚为输入模式，并开启上拉电阻 (PUD_UP)
        # 这样当传感器没有信号输出时，引脚默认保持高电平，防止干扰
        GPIO.setup(self.SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        print(f"[IRSensor] 初始化完成。")
        print(f"   - 信号引脚: BOARD {self.SENSOR_PIN}")
        print(f"   - 触发逻辑: {'低电平(LOW)' if self.TRIGGER_LEVEL == GPIO.LOW else '高电平(HIGH)'}")

    def is_object_detected(self) -> bool:
        """
        检查是否检测到物体。
        :return: True (检测到物体/被遮挡), False (无物体/通畅)
        """
        # 读取引脚状态
        current_state = GPIO.input(self.SENSOR_PIN)
        
        # 判断是否等于触发电平
        return current_state == self.TRIGGER_LEVEL

    def cleanup(self):
        """释放 GPIO 资源"""
        # 注意：通常由主程序统一 cleanup，但这里提供单独清理的方法以防万一
        try:
            GPIO.cleanup(self.SENSOR_PIN)
        except Exception:
            pass
        print("[IRSensor] 资源释放 (Pin 37)")
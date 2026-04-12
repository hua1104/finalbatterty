# 项目总览（统一说明）

本项目包含 **服务器端** 与 **客户端** 两部分。  
客户端负责采集、AI判定与上传，服务器端负责接收数据、入库、展示与管理。

---

**目录结构**
```
better/
  server/                # 服务端代码与依赖
  client/                # 客户端代码与依赖
  tools/                 # 辅助脚本（可选）
  docs/                  # 文档资料
  dist/                  # 已整理好的部署目录（server/client）
  logs/                  # 运行日志（自动生成）
  __pycache__/           # Python缓存（自动生成）
```

---

**运行流程（端到端配合）**
1. 客户端 `client/final.py` 采集图像、调用 `client/ai_detector.py` 识别缺陷  
2. 客户端将结果与图片上传到服务器  
3. 服务端 `server/server.py` 接收上传、写入 MySQL  
4. 服务端提供管理界面与查询/统计接口（网页展示）

---

**服务器端（server/）说明**
- `server/server.py`  
  服务端主程序：Flask + Waitress，包含登录、上传、查询、统计、导出等接口与页面。
- `server/requirements.txt`  
  服务端依赖列表。
- `server/server_config.example.json`  
  配置模板。实际部署时复制成 `server_config.json` 并填写真实参数。

**关键依赖说明**
- 数据库：MySQL 8.0  
- 上传目录：`UPLOAD_FOLDER` 对应的磁盘路径必须可写

---

**客户端（client/）说明**
- `client/final.py`  
  客户端主程序：UI、采集、上传、硬件控制。
- `client/ai_detector.py`  
  AI推理封装。
- `client/best.pt`  
  模型权重文件。
- `client/config.json`  
  客户端配置（服务器IP、摄像头参数、模型路径等）。
- `client/requirements_client.txt`  
  客户端依赖列表。
- `client/ir_sensor.py` / `client/stepper_driver.py` / `client/sorter_motor.py`  
  硬件驱动模块（与现场硬件配合）。

---

**依赖分类（三种类型）**
1. **服务器端运行依赖**  
   - Flask  
   - mysql-connector-python  
   - waitress  
   - pandas  
   - openpyxl  
   - Werkzeug

2. **客户端运行依赖**  
   - Pillow  
   - requests  
   - opencv-python  
   - numpy

3. **可选/工具类依赖（非运行必需）**  
   - `tools/` 下脚本用于测试、建索引、生成文档  
   - `docs/` 内为使用说明与资料  
   - `dist/` 为已整理好的可部署目录  

---

**辅助目录说明**
- `tools/`  
  - `tools/mock.py`：测试造数据  
  - `tools/sqlt.py`：创建索引  
  - `tools/gen_doc.py`：文档生成  
- `docs/`  
  - `docs/DEPLOY.md`：部署说明  
  - 其他说明文档与材料  
- `dist/`  
  - `dist/dist_server/`：服务器端可部署包  
  - `dist/dist_client/`：客户端可部署包

---

**快速开始**
请直接阅读 `docs/DEPLOY.md`，按服务器端与客户端步骤部署即可。

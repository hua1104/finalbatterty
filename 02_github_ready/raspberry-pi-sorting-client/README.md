# Raspberry Pi Sorting Client

这个目录主要来自你桌面的 `树莓派`，我把它整理成了一个更适合单独上传 GitHub 的硬件端仓库。

## 本次整理做了什么

- 保留了树莓派端的核心 Python 代码和模型文件 `best.pt`
- 去掉了测试图片、安装包、快捷方式、临时文件
- 额外补入了下面 3 个文件，使目录更完整
  - `ai_detector.py`
  - `sorter_motor.py`
  - `requirements.txt`

这 3 个补入文件来自 `better/client`，原因是原 `树莓派` 目录中的 `final.py` 明确依赖 `ai_detector.py`，并且代码导入的是 `sorter_motor.py`，但原目录里只有 `sorter_mortor.py`。

## 上传前建议

- 公开上传前，请先检查代码里的 `SERVER_IP`、`SERVER_URL` 等硬编码配置。
- 如果你准备长期维护这个仓库，建议后续把这些网络参数统一改成配置文件，而不是直接写在脚本里。

## 说明

- 原始的 `树莓派` 目录没有被改动。
- 说明书被单独放到了 `../../04_reference_materials/raspberry-pi-docs/`。

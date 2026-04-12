# Project Workspace

这个 `project` 文件夹是我根据你桌面上的 3 个目录重新整理出来的分类版，原始目录没有被改动。

## 分类说明

- `01_github_ready/battery-sorting-system`
  - 以 `better` 为主整理出的正式版主仓库。
  - 保留了 `client`、`server`、`tools`、精简后的 `docs`。
  - `client/config.json` 已改成占位版并默认忽略提交，公开上传时建议只保留 `config.example.json`。
  - 这是最适合优先上传 GitHub 的版本。

- `02_github_ready/raspberry-pi-sorting-client`
  - 以 `树莓派` 为主整理出的硬件端仓库。
  - 去掉了测试图片、安装包、临时文件。
  - 因为原目录缺少 `ai_detector.py`，并且代码导入的是 `sorter_motor.py` 但原文件名是 `sorter_mortor.py`，这里额外补入了来自 `better/client` 的 `ai_detector.py`、`sorter_motor.py`、`requirements.txt`，方便后续单独传 GitHub。

- `03_legacy_code/batteryserver-legacy`
  - 从 `BatteryServer` 中提取出的旧版核心代码归档。
  - 适合留作历史版本参考，不建议直接公开上传。

- `04_reference_materials`
  - 存放说明书、软著文档、CAD、其他资料型内容。
  - 这些内容建议单独归档，或者放私有仓库，不要和主代码仓库混在一起。

## 没有复制进来的内容

为了让整理后的目录更适合 GitHub，我没有把下面这些内容直接放进新仓库：

- `BatteryServer/.venv`
- `BatteryServer/PHOTO`
- `BatteryServer/received_images`
- `BatteryServer/localhost.sql`
- `BatteryServer/电导不是韩导 软著资料`
- `better/dist`
- `better/logs`
- `better/__pycache__`
- `树莓派` 里的测试图片、安装器、快捷方式、临时 Office 文件、零散文本草稿

## 上传建议

建议上传顺序：

1. 先上传 `01_github_ready/battery-sorting-system`
2. 再按需要上传 `02_github_ready/raspberry-pi-sorting-client`
3. `03_legacy_code` 和 `04_reference_materials` 更适合做私有备份或单独归档

## 额外提醒

- `02_github_ready/raspberry-pi-sorting-client` 里仍然保留了原代码中的硬编码 IP，请在公开上传前再检查一遍。
- `03_legacy_code/batteryserver-legacy` 中可能还包含旧的地址、示例账号或测试逻辑，公开上传前建议再做一次人工筛查。

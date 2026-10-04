# RAW / HEIC 转 JPG 工具

这是一个 Windows 桌面批量转换工具，可以把 `.nef`、`.nrw`、`.dng`、`.heic`、`.heif` 文件转换为 `.jpg`。

## 功能

- 选择单个或多个 NEF / NRW / DNG / HEIC / HEIF 文件
- 选择文件夹批量加入，可包含子文件夹
- 支持不同格式同时加入列表并一起转换
- 选择 JPG 保存路径
- 设置 JPG 输出质量
- 设置并行转换任务数，最多可使用当前电脑的全部逻辑核心
- RAW 文件可使用相机白平衡
- 可按照片拍摄时间命名，例如 `20260105193802.jpg`；无拍摄时间元数据时使用原文件创建时间
- 可选择是否覆盖已有 JPG
- 后台转换，界面显示进度和日志

## 安装依赖

打开 PowerShell，进入本目录后运行：

```powershell
pip install -r requirements.txt
```

如果 `pip` 不在 PATH，可以使用本机 Python 的完整路径，例如：

```powershell
& "C:\Users\Jercy\AppData\Local\Programs\Python\Python311\python.exe" -m pip install -r requirements.txt
```

## 启动

双击 `启动NEF转JPG工具.bat`，或在 PowerShell 中运行：

```powershell
python app.py
```

如果 `python` 不在 PATH，可以运行：

```powershell
& "C:\Users\Jercy\AppData\Local\Programs\Python\Python311\python.exe" app.py
```

## 说明

RAW 格式转换需要 `rawpy` 和 `Pillow`，HEIC / HEIF 转换需要 `pillow-heif` 和 `Pillow`，读取 RAW 拍摄时间需要 `ExifRead`。如果界面能打开但转换失败并提示缺少依赖，请先安装依赖。

勾选“按拍摄时间命名”后，程序优先使用照片元数据中的拍摄时间。同一秒拍摄多张照片或目标文件已存在时，会自动生成 `_1`、`_2` 等后缀，避免相互覆盖。

并行任务数越高，批量转换通常越快，但会明显增加 CPU 和内存占用。大尺寸 RAW 如果全核心转换导致电脑卡顿，可以把并行任务数降到 4 到 8。

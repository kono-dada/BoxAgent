# Qwen3.5-0.8B 本地读屏预实验

本文保留 2026-09-13 的独立图片预实验数据。后续已接入桌宠常驻窗口摘要 worker，当前默认 960 像素、15 秒周期、512 token；使用方式见 [README](../README.md)。尚未验证多场景质量及多小时常驻稳定性。

## 部署内容

- 机器：M4 MacBook Air，16 GB 统一内存，macOS 26.6.2。
- 模型：[ModelScope 的 `mlx-community/Qwen3.5-0.8B-4bit`](https://modelscope.cn/models/mlx-community/Qwen3.5-0.8B-4bit)。13 个文件放在 `models/qwen3.5-0.8b-mlx/`，下载后逐个按仓库 SHA-256 校验。
- 推理环境：项目内 `.venv/`，Python 3.12.9，`mlx-vlm==0.7.0`、`mlx==0.32.2`、`mlx-metal==0.32.2`。安装使用 `uv`，没有启动常驻服务。
- 当时项目目录占用：`.venv/` 约 587 MiB，`models/` 约 635 MiB。两者合计约 1.2 GiB；未把已有的 `uv` 缓存算入，也没有测安装前后的系统盘净增量。

重建环境：

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --index-url https://mirrors.aliyun.com/pypi/simple 'mlx-vlm==0.7.0' 'mlx==0.32.2' 'mlx-metal==0.32.2' requests jinja2
zsh scripts/download-qwen-mlx.zsh
```

本机测试需要让 MLX 访问 Metal GPU。当前 Codex 命令沙箱内 GPU 不可见；在普通 macOS 终端中运行以下命令即可：

```sh
zsh scripts/probe-qwen-mlx.zsh /绝对路径/截图.png
```

脚本只向本地模型传入图片，不向远端提交截图。首次准备环境和下载模型需要联网；之后传入本地模型目录运行。

## 已测结果

使用一张真实的当前桌面截图，原图 2940×1912；另用 `sips -Z 1280` 制作 1280×832 的缩小版。两次均为独立进程、非思考模式、温度 0、最多 180 个输出 token。提问是识别前台应用、当前对话标题，并摘录两条可见中文文字。

| 图片 | 输入 token | 整次命令墙钟时间 | 输出 token | MLX 报告峰值内存 | 观察 |
| --- | ---: | ---: | ---: | ---: | --- |
| 原图 | 5,561 | 19.26 秒 | 63 | 2.083 GB | 读出 ChatGPT 界面、会话标题与两条可见文字。 |
| 缩小版 | 1,081 | 5.15 秒 | 99 | 1.717 GB | 仍读出应用、标题与主要文字；长句摘录有轻微错字。 |

### 输入隐私与验证边界

测试输入为个人电脑截图，不随代码仓库分发。原始截图和逐字回答只保留在已忽略的本机私有目录中；此处仅保留性能数据与质量结论。缩小版出现少量文字识别错误，应用判断也存在菜单栏名称与内容界面的混淆。

前一次未开启详细统计的试跑也读取了截图，但把应用名写成 `CodeX`，且没有按要求给足两处依据。上表只有同一张截图各一次，输出长度也不同；不能由此推断稳定延迟、质量或缩放的通用最优值。该截图是运行时的 Codex 对话界面，不能代表视频、代码、小字号网页或多窗口。下一轮应对同一组真实截图重复测试，并把窗口信息／OCR 作为对照。

原始运行文件留在被 Git 忽略的 `results/`；历史截图副本已移至 `.runtime/private/docs-assets/`。模型和 `.venv/` 也被忽略。

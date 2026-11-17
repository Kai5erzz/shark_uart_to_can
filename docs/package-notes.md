# 开发包说明

打包日期：2026-11-02，Asia/Shanghai。名称 `shark_uart_to_can_dev_20261102.zip`，这是当前开发快照，外部双向透传验收尚未通过。

## 内容与入口

| 路径 | 内容 |
|---|---|
| `README.md` | 工程总览与索引 |
| `docs/user-guide.md` | 接线、终端、编译烧录、模式/按键/ID、两端上位机、测试与排查 |
| `docs/protocol.md` | RAW、ASCII、二进制协议速查、完整字段和报文示例 |
| `uart_to_can/` | 应用源码、CubeMX生成代码、IOC、启动/链接、HAL/CMSIS/FreeRTOS/USB源码 |
| `cmake/`、根CMake文件 | ARM GCC构建工具链与Debug/Release预设 |
| `.vscode/`、`openocd/` | 编译、DAPLink烧录/调试、上位机任务与复位处理 |
| `tools/serial_assistant.py` | SHARK助手：ASCII/二进制、定时发送、性能、RAW双向长文本 |
| `tools/can_monitor/` | CAN Monitor完整Python源码及测试，可在本包内独立启动 |
| `tests/`、`tools/test_host.sh` | 固件逻辑、协议及真实Tk/PTY主机测试 |
| `build/Debug/uart_to_can/`、`build/Release/uart_to_can/` | 已构建的ELF、HEX、BIN、map；不含CMake缓存/中间文件 |
| `build/performance/` 与选定日志 | 历史性能、当前RAW成功/失败记录；均按原日期保留 |
| `PACKAGE.json`、`SHA256SUMS` | 打包时生成的版本/组成说明和逐文件校验 |

CAN Monitor从此前参考工程 `code_914/HNU_Embedded_Framework/tools/can_monitor/` 收入本包，除本文档说明外保留原功能。运行 `python3 tools/can_monitor/app.py`，无需保留参考工程目录。原厂SDK二进制和本机libusb运行库没有打入包；旧CDC串口、SocketCAN、演示模式不需要该SDK。需要原厂原生USB时用 `tools/can_monitor/setup_sdk.py` 按校验下载，见其README。

完整STM32CubeG4安装包仍在原D盘仓库，未复制数百MB归档。普通编译依赖已随源码提供；重新生成IOC才需要CubeMX和匹配的完整固件包。第三方原有许可证保留；ST包许可清单在 [STM32CubeG4-Package-license.md](STM32CubeG4-Package-license.md)，组件分别按其原始许可使用。本打包不为第三方组件重新授权。

## 当前版本状态

本包包含2026-11-02新增的USART2运行时波特率设置。它尚未烧录到实板；此前2026-10-31实板测试用的是旧镜像，外部RAW双向验收仍未通过。

| 项目 | 当前证据 |
|---|---|
| Debug / Release构建 | 成功；Release Flash51356 B、RAM28512 B |
| Release BIN SHA-256 | `35103c5724dfbc97bb3f8e049283439b1ef5cc7c445cb56cf3d831a05e3288e8` |
| 主机测试基线 | 3个C测试程序、SHARK25项Python、Monitor31项Python通过 |
| 当前RAW实板 | 16KiB内部回环一致；外部30B短文本曾成功，后续长文本丢字节/反向超时 |
| 终端电阻 | 用户提出可能只有一端120Ω；应两端各120Ω、断电等效约60Ω；修正后未复测 |
| ID | 无记录默认1；开发板上次保存5，正常烧录保留；包内不附ID Flash日志 |
| 仍需验证 | 稳定外部单帧与长文本双向、三路压力、bus-off恢复操作、真实掉电保存与USB重插 |

旧USB性能报告的成功数值是更早固件的历史基线，不应作为当前RAW实板结果。详见 [当前RAW实板记录](uart2-raw-20261031.md)、[验证记录](validation.md)。

从本包独立解压后，Release已无缓存干净构建；生成的BIN/HEX与工作区镜像逐字节一致，3个C测试、SHARK25项及CAN Monitor31项均通过。归档内326个文件的大小和SHA-256、内层清单及外层校验值均已核对。以上不连接硬件，USART2运行时波特率设置尚未实板验证。

## 校验和重新打包

先在ZIP同目录校验外层归档，解压后可校验包内所有文件：

```bash
sha256sum -c shark_uart_to_can_dev_20261102.zip.sha256
cd shark_uart_to_can_dev_20261102
sha256sum -c SHA256SUMS
```

Windows可用 `Get-FileHash .\shark_uart_to_can_dev_20261102.zip -Algorithm SHA256` 对比旁边的 `.sha256` 文件。编译会更新生成文件，应先校验后编译。

后续重新构建并更新 `docs/build_artifacts.json` 后，在工程根目录执行：

```bash
python3 tools/package_project.py
```

脚本按Asia/Shanghai当天日期命名，默认输出 `dist/`，只收指定源码、预编译镜像和测试证据。它验证镜像SHA-256与现有清单一致，排除中间文件、缓存、历史Flash备份和第三方本机SDK；未更新镜像清单时会拒绝混装。ZIP内每个文件的字节数/SHA-256可由清单核对。

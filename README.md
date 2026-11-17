# SHARK UART / USB / CAN 模块

交付入口：[使用教程](docs/user-guide.md) · [通信协议与报文示例](docs/protocol.md) · [打包说明与版本状态](docs/package-notes.md)。完整开发包包含固件源码、HAL/CMSIS/FreeRTOS/USB依赖、IOC、VS Code任务、两端上位机、预编译镜像和测试记录；包内可直接运行 `python3 tools/can_monitor/app.py`。

基于 STM32G431CBT6、FreeRTOS、TJA1044。USART1 固定为 **921600 8N1**，USART2 默认为 **921600 8N1**且可运行时配置；Classical CAN 为 **1 Mbit/s**，USB 为 CDC 虚拟串口。USART1 / USB 支持 ASCII 和二进制协议；USART2 专用于外部设备的**原始字节双向 UART↔CAN 透传**，无需遵循 SHARK 协议。USB↔USART1 模式支持任意原始字节。

可从CH340/USART1或USB CDC的ASCII命令入口查询并临时设置USART2波特率，例如 `baud uart2 460800`；复位后恢复921600。配置语法和限制见[通信协议](docs/protocol.md#ascii-指令)。

## 工作模式与三路输入

| 模式 | 数据路径 | 模式 LED | SIGNED_LED |
|---|---|---|---|
| `uart-can`（上电默认） | USART1、USART2、USB 都可提交 CAN；任务先处理 UART | LED1 | 1 Hz |
| `usb-can` | USART1、USART2、USB 都可提交 CAN；任务先处理 USB | LED2 | 0.5 Hz |
| `usb-ttl` | USB↔USART1 双向透明传输；USART2 保持原始字节↔CAN | LED3 | 2 秒周期呼吸 |
| ID 设置（临时状态） | 保持当前转发路径 | 三灯同步闪烁，次数表示候选 ID | 0.2 Hz |

所有 LED **低有效**。两种 CAN 模式均保持三路监听，CAN 接收数据同时报告到各协议端口；USART2 仅输出匹配板上 ID 的标准数据帧 payload。透明模式的 USB / USART1 不解析指令，也不插入日志；此时请用按键退出透明模式，再经 USB / USART1 配置。

USART1 / USB 默认用 ASCII 报告 CAN；`wire binary` 改用二进制报告。两种报告格式下都接受 ASCII 和二进制指令。它们也可设为 `wire raw`。USART2 上电即固定为 RAW：输入立即按最多 8 字节分包，以板上标准 CAN ID 发出；反向仅输出匹配 ID 的原始 payload，不添加协议头、换行或日志，不执行输入中的指令。原始流不保留设备消息边界。详见 [协议文档](docs/protocol.md)。

## 按键设置与 ID 保存

按键 PA0 低有效，25 ms 消抖，动作在**松开时**执行：

- 按住 **1～不足 3 秒**：循环 `uart-can → usb-can → usb-ttl`。
- 按住 **至少 3 秒**：进入 ID 设置；工作模式保持原值。
- 设置状态下短按：第 1 次选 ID 1，第 2 次选 ID 2，以此类推，32 次后回到 1。
- 设置状态下按住至少 1 秒：保存候选 ID 并退出；没有点按则取消。
- 设置状态下 30 秒无操作：取消，保留原 ID。

ID 范围 **十进制 1～32**，直接对应标准 CAN ID `0x001～0x020`。按键确认后掉电保存。命令 `id 7` 只改变运行值，`save` 才写入 Flash。显式指定 CAN ID 的帧仍使用帧里的 ID；`write`、`test`、原始流和二进制 DEFAULT_ID 标志使用板上 ID。工作模式和端口格式不保存，上电恢复默认。

保存使用两页带 CRC 的 Flash 日志，旧记录在新记录写成前保留；重复保存相同 ID 不写 Flash。地址 `0x0800F000～0x0800FFFF`，应用链接范围限制为前 **60 KiB**。正常 VS Code 烧录保留配置区；全片擦除会清除 ID。保存期间同 Bank Flash 擦写会暂停 CPU 取指，请暂停高速输入后配置，不把保存操作计入实时转发场景。

## 编译、烧录与 VS Code

```bash
cmake --preset Debug
cmake --build --preset Debug
cmake --preset Release
cmake --build --preset Release
```

输出：`build/Debug/uart_to_can/`、`build/Release/uart_to_can/` 中的 ELF / HEX / BIN / map。需要 ARM GCC、CMake ≥3.22、Ninja、OpenOCD。优先使用 Release 做性能测试。

VS Code 已配置：

- **Ctrl+Shift+B / Build**、**Build Release**：配置并编译。
- **Flash (DAPLink)**、**Flash Release (DAPLink)**：先构建，再下载、校验、复位。
- **Repair Boot Option (DAPLink)**：启动选项被恢复为默认值、复位进入系统 Bootloader 时，写入一次 `nBOOT0=1` 并重载选项字节；已修好时只读检查，不写 Flash。
- **F5 / Debug STM32G431 (DAPLink)**：使用 Cortex-Debug。
- **Serial Assistant**：图形助手。
- **CH340 / CDC Self Test**、**Binary / CAN Self Test**：收发与内部回环。
- **Serial Latency / Throughput**：二进制回显 RTT 和窗口吞吐测试。
- 助手的 **CAN 延迟 / 吞吐测试** 页：配合 CAN Monitor 测量外部 CAN 完整往返。
- 助手的 **USART2 RAW / 双向长文本** 页：原始 ASCII 长文本往返校验、独立 CAN→USART2 校验及结果导出。
- **USB - USART1 Full Duplex Test**：需已进入 `usb-ttl`，并连接两条串口。
- **Host Tests**：Linux 主机自动测试；**Regenerate (CubeMX)**：重新生成底层工程。

DAPLink：SWDIO / SWCLK / GND / 目标电压参考，SWD 500 kHz。当前探针组合的原生 `reset halt` 会超时，已使用复位入口临时硬件断点处理：

```bash
openocd -f openocd/daplink_stm32g4.cfg \
  -c 'shark_program {build/Release/uart_to_can/uart_to_can.elf}'
```

`shark_program` 会先读 `FLASH_OPTR`。若启动选项不再是主 Flash（`nSWBOOT0=0, nBOOT0=0`，复位停在系统 Bootloader `0x1FFFxxxx`），会立即报错并提示先执行一次修复，而不是等到复位入口超时：

```bash
openocd -f openocd/daplink_stm32g4.cfg -c shark_fix_boot
```

修复只写 `nBOOT0`（bit 27，掩码 `0x08000000`），RDP/BOR/WRP/PCROP 等其他选项位保持不变；已修好时该命令只读不写。2026-11-06 发现启动选项又回到默认值，现场修复并重新烧录 Debug 镜像（58,412 B 写入/校验通过），过程见 [验证记录](docs/validation.md) 和 `docs/daplink_flash_20261106.log`。

不要改回此前失败的 `program ... verify reset exit`。复位处理、芯片标识差异和此前启动选项修复记录见 [验证记录](docs/validation.md)。

## 串口助手与快速验证

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/serial_assistant.py --list
python3 tools/serial_assistant.py
python3 tools/serial_assistant.py --port /dev/ttyUSB0 --terminal
```

Windows 将 `python3` 换成 `python`、端口换成 `COM5` 等。GUI 提供文本 / HEX 原始发送、协议解码、二进制 CAN / 64 字节分包、ID 设置 / 保存、工作模式和 CAN 诊断控制。原始流请勾选“透明/原始流”，关闭协议解码；快捷命令不会再注入原始流。`wire raw` 的最后一条 `OK wire` 是配置边界响应，请读完再传输业务数据。GUI 与命令行测试不要同时占用同一串口。

GUI 的“定时发送”支持 **文本 / HEX、二进制 CAN、二进制回显**：

1. 连接串口，填好对应发送栏内容。例如文本栏填 `send 123 1122334455667788`，勾选 CRLF。
2. 在“定时发送”选择“文本 / HEX”，设置间隔（默认100毫秒，范围1～3600000毫秒）和次数（0表示持续发送）。
3. 点击“开始定时”，立即发送第一条，随后按间隔重复；显示已发送次数，达到次数后自动停止。点击“停止定时”可提前停止。

选择二进制 CAN / 回显时使用上方二进制栏的 ID、HEX 数据和标志，每次生成新序号。启动时固定本轮内容及选项，修改后需停止并重新开始。断开串口、发送失败、切换原始流选项或执行快捷配置命令会停止定时发送。计数代表主机完整写入串口的次数，CAN是否成功仍需检查对端及设备ACK。定时由电脑GUI调度，实际间隔会受到系统负载与串口写入影响；错过周期会跳过，不集中补发。

先烧录新版，再依次测试：

```bash
# 任一协议端口，验证 ASCII 随机回显及 CAN 内部回环
python3 tools/serial_assistant.py --port /dev/ttyACM0 --self-test --can-loopback

# 二进制、标准/扩展/空 CAN 帧、64 字节分包；恢复测试前 CAN 状态/端口格式
python3 tools/serial_assistant.py --port /dev/ttyACM0 --binary-test --can-loopback --rounds 256

# 实测主机到 MCU 再回到主机的 RTT 和回显吞吐
python3 tools/serial_assistant.py --port /dev/ttyUSB0 --benchmark --rounds 1000 --size 64

# 先从 USB / USART1 执行 mode usb-ttl，或使用按键，再连接原生 USB 与 CH340
python3 tools/serial_assistant.py --port /dev/ttyACM0 --peer /dev/ttyUSB0 \
  --bridge-test --rounds 1000 --size 256
```

前版固件经 USB 的外部 1 Mbit/s CAN 往返已用达妙 USB-CAN 实测，结果见下节。当前 USART2 RAW Release 已烧录，16KiB内部回环通过；外部短包曾成功，但长文本丢字节且后续反向失败，**当前外部双向测试尚未通过**，见 [RAW实板记录](docs/uart2-raw-20261031.md)。仍需完成 USART1 / USB 协议回归、USART2 RAW 双向长文本、三路同时提交 CAN、ID断电保存、USB重插及LED观察。外部 CAN 共地、总线两端各120Ω；内部回环不验证 TJA1044 或总线接线。

透明测试通过两端口并发发送随机字节并逐字节校验，不会自动切换模式。二进制/ASCII 回环测试不保存 Flash；回环会重置 CAN 并取消未发送帧，应在停止业务流量后运行。

## USART2 RAW 双向透传测试

启动两端 GUI：SHARK 助手连接**实际接在 USART2 的 USB-TTL 适配器**，CAN Monitor 连接 USB-CAN，CAN 设为 1 Mbit/s。

1. **往返长文本**：Monitor 打开 **USART2 RAW / ASCII 双向测试** 页，填板上 ID（默认 `001`），开启 **RAW测试：收到即同ID原样回传**。SHARK 打开 **USART2 RAW / 双向长文本**，生成 16384 字节，点击 **开始RAW双向测试**。Monitor 连续显示拼接后的 ASCII；SHARK 逐字节比对完整返回，全部一致才显示 PASS。可导出 JSON 和收发文本。
2. **独立反向**：关闭 Monitor 的 RAW 回传，SHARK 点击 **清空接收**，在发送原文框填写预期文本；Monitor 下方填相同文本，点击 **发送ASCII到USART2**。SHARK 收齐后点击 **比对独立反向接收**，检查 PASS。

USART2 默认 RAW，无需发送配置命令；两端都不额外添加换行。默认每 5 ms 写 32 字节、在途上限 256 字节用于先验证完整性；这不是最大吞吐结论。适配器 TX→PA3、RX→PA2，使用 3.3V TTL 并共地。完整步骤、错误排查和测试边界见 [双向透传测试指南](docs/uart2-raw-test.md)。

## CAN Monitor 往返延迟与吞吐

重启旧版助手以载入新页面。先用 `python3 tools/serial_assistant.py --list` 识别设备；此前 SHARK 原生USB、DAPLink串口均曾使用 `/dev/ttyACM1`，不能仅凭编号判断用途。

1. 在 CAN Monitor 中连接分析仪，CAN 设为 **1 Mbit/s**，勾选 **SHARK 测试帧立即回传**。默认请求 ID `6E0`、回复 ID `6E1`，都按十六进制填写。
2. 在 SHARK 助手中连接 SHARK，进入 **CAN 延迟 / 吞吐测试** 页，选择“低载 RTT”，点击“开始性能测试”。默认 1000 样本、间隔10 ms、超时200 ms、窗口1。
3. 查看平均值、P50/P95/P99、最大值、标准差、相邻 RTT 差值的均值、超时/拒绝/重复/迟到，以及最近200条有效回复曲线。“导出 JSON / CSV”保存配置、设备状态、前后 `stats` 和每个样本。
4. “持续吞吐”使用间隔0、窗口16，最多同时存在16个未完成请求，用来寻找过载边界；“串口回显基线”只测试 SHARK ECHO，无需分析仪。

每帧8字节，包含本次随机标记和32位序号；只接受匹配回复 ID、标记与序号的帧。CAN Monitor 仅回传匹配请求 ID 的8字节标准数据帧，忽略自己的TX回显、回复ID、远程帧、扩展帧和FD帧。回传在接收线程内执行，不等待界面刷新或写盘。测试前请停止业务发送，并为测试保留这两个 ID。

测试线程独占 SHARK 串口，自动停止定时发送并暂停普通接收线程；临时设置二进制报告和 CAN 正常模式，结束、取消和错误退出时尝试恢复原配置，不写 Flash。断开/关闭会先取消测试并恢复配置。此协议性能页使用 USB / USART1；透明模式先按键切回 CAN 模式，`wire raw` 端口需通过另一路协议端口恢复格式或重启。USART2 使用上面的 RAW 测试页。

命令行也能运行，两个终端各执行一端。CAN Monitor 源码已收进本工程：

```bash
# 终端一：立即回传，Ctrl+C停止；也可使用该工程原有GUI并勾选回传
python3 tools/can_monitor/echo_server.py \
  --port /dev/ttyACM0 --output build/performance/monitor.json

# 终端二：1000次CAN往返，同时生成JSON与CSV
python3 tools/serial_assistant.py --port /dev/ttyACM1 --can-performance \
  --rounds 1000 --interval-ms 10 --window 1 --output build/performance/can.json

# 窗口吞吐；有超时、拒绝、配置恢复错误时，CLI返回非零退出码
python3 tools/serial_assistant.py --port /dev/ttyACM1 --can-performance \
  --rounds 3000 --interval-ms 0 --window 16 --output build/performance/throughput.json

# 同一入口的8字节串口回显基线
python3 tools/serial_assistant.py --port /dev/ttyACM1 --serial-baseline \
  --rounds 1000 --output build/performance/baseline.json
```

RTT从主机串口写入前计时，到完整匹配回复到达后结束，使用 `monotonic_ns`；包括主机调度、USB/UART、双程CAN和分析仪回传开销。**这是完整往返时间，不能直接除以2作为MCU单向延迟。** P50/P95/P99仅统计超时前到达的有效回复，缺失另行计数；运行中的“未完成”包含在途请求。帧率为有效回复数/测量时长，有效载荷速率为帧率×8，不包含协议开销，也不是CAN物理线速。预热10帧不计入样本统计；前后固件计数和Monitor计数包含预热。

2026-10-31 USB实板历史基线（早于 USART2 RAW 改动）：低载1000/1000成功，平均 **1.450 ms**、P99 **1.861 ms**；窗口1连续发送约874次有效往返/秒。更大突发窗口出现超时，限速测试结果及两端计数见 [性能报告](docs/performance-20261031.md)。这些数值不代表新固件已通过实板验证。

## 低延迟实现与吞吐边界

UART RX 使用 **64 字节循环 DMA + HT / TC / IDLE**，连续输入每 32 字节触发处理，在 921600 8N1 下约 **347 μs**；短包在 IDLE 时立即唤醒任务。TX 使用 DMA 和独立发送缓存。UART / USB / CAN IRQ 通过任务通知直接唤醒高优先级转发任务，任务没有固定 1 ms 数据轮询延迟，也不为拼满 CAN 帧等待超时。

USB FS 使用片内 PMA、USB 中断和缓冲区；本器件这条 USB CDC 路径没有可直接使用的 UART 式 DMA。USB OUT 在接收缓冲不足时延迟重挂接，通过 NAK 向主机施加背压。USB↔UART1 的 USB→UART 拥堵可由此减速；物理 UART 没有 RTS/CTS，持续过量输入仍可能丢字节。

每端口 RX / TX 环各 1024 字节（可用 1023），UART / USB TX 独立缓存 256 字节；CAN 软件 TX / RX 可用 31 / 63 帧。协议发 CAN 队列满会报错，原始流等待可用队列；CAN 报告输出拥堵时整条丢弃并累计计数。主机需限制总速率并检查 ACK / `stats`，不把三路总输入带宽视为 CAN 可用吞吐。

921600 8N1 每方向理论上限 **92,160 字节/秒**；CAN 1 Mbit/s 还包含仲裁、CRC、ACK、帧间隔和位填充，实际业务吞吐更低。二进制每包固定开销 16 字节，64 字节 STREAM 批量比逐个 8 字节包更省串口带宽，但接收方需等完整包后才提交。短实时消息优先 CAN_TX，持续大块数据可用 STREAM_TX。总线仲裁、USB 驱动和主机调度都会影响端到端时延。

`stats` 提供队列/丢包/错误、TEC/REC、空闲堆与栈余量。`dispatch_us_max` 只估计 DMA / USB 接收回调到任务处理的时间，不是串口引脚到 CAN 引脚的延迟。助手 RTT 也包含主机调度；真正 UART→CAN 单向延迟应使用逻辑分析仪或示波器同时测量输入 UART 与 CAN。

## 引脚、时钟与 CubeMX

| 功能 | 引脚 / 配置 |
|---|---|
| USART1 / CH340 / USB↔TTL | PA9 TX、PA10 RX，921600 8N1，DMA |
| USART2 / 原始字节双向↔CAN | PA2 TX、PA3 RX，默认921600 8N1（可运行时配置），DMA，标准 ID=板上 ID |
| USB CDC | PA11 DM、PA12 DP |
| FDCAN1 | PB8 RX、PB9 TX，Classical CAN 1 Mbit/s |
| TJA1044 STB | PB7，低电平正常，高电平待机 |
| 按键 | PA0，低有效 |
| LED1 / LED2 / LED3 / SIGNED | PA5 / PB0 / PB10 / PB3，低有效 PWM |
| HSE | PF0 / PF1，32 MHz |
| SWD | PA13 / PA14 |

HSE 32 MHz → PLL M4 / N36 / R2 → SYSCLK 144 MHz；PLLQ /6 → USB / FDCAN 48 MHz。CAN 分频3、Seg1=13、Seg2=2、SJW=2，采样点87.5%。UART 配置921600，实际分频约923077（+0.16%）。FreeRTOS SysTick1kHz；HAL Tick TIM6；数据 IRQ 优先级5。应用任务3072字节栈，heap_4 8192字节，MSP2048字节；运行余量需通过 `stats` 实测。

固件仓库在 **D 盘**，未放系统根目录：

```text
/media/kaiser/D盘2/STM32Cube/Repository/STM32Cube_FW_G4_V1.6.3
```

ST 官方包校验见 [firmware_packages.json](docs/firmware_packages.json)。使用 CubeMX6.18.1，IOC 在 `uart_to_can/uart_to_can.ioc`；应用源码在 `uart_to_can/App`。重新生成：

```bash
python3 tools/regenerate.py
```

脚本保留 USER CODE，并重新限制链接 Flash 为60KiB，防止 CubeMX 覆盖 ID 保留区。直接在 GUI 生成后也必须保留这个链接设置；推荐统一使用脚本。原始 IOC 在 `docs/uart_to_can.original.ioc`。源码阶段备份在 `build/backups/before-product-20261031.tar.gz`。

当前开发版的主机测试与构建结果、实板未验证项目见 [验证记录](docs/validation.md)。

# USART2 ↔ CAN 原始数据双向透传测试

USART2 固定为 RAW，三种工作模式下均生效。输入无需遵循 SHARK 指令协议；包括 ASCII、零字节及外设自身协议的全部字节，都会按当时可用的数据立即拆成最多8字节的标准 CAN 帧。短包不补零、不等待凑满；反向只将匹配板上 ID 的标准数据帧 payload 原样输出到 USART2。没有附加帧头、CRC、换行或调试日志。

## 接线和启动

| 连接 | 接法 |
|---|---|
| USB-TTL TX | SHARK PA3 / USART2 RX |
| USB-TTL RX | SHARK PA2 / USART2 TX |
| USB-TTL GND | SHARK GND |
| 电平与串口 | 3.3V TTL，921600、8N1，无流控 |
| CAN | 两端 CANH↔CANH、CANL↔CANL、GND 共地，收发器上电 |
| CAN 位速率 | 两端 Classical CAN，1 Mbit/s，两端各120Ω终端 |

先使用 VS Code **Flash Release (DAPLink)** 烧录本次固件。标准烧录保留 Flash 中的 ID；无有效配置时为1。ID 序号十进制1～32对应十六进制 `001～020`，如序号5填 `005`。可在 CAN 模式下经 USB / USART1 发送 `id` 查询；USART2 不执行此命令。

分别启动：

```bash
# SHARK 工程
python3 tools/serial_assistant.py

# 同一工程中的 CAN Monitor（另一个终端）
python3 tools/can_monitor/app.py
```

SHARK 上方选择的是**接到 USART2 的 USB-TTL 串口**，不是 SHARK 原生 USB 或板载 CH340。DAPLink 自带串口也可以，但必须实际连接 TX/RX。端口号随插拔变化，先识别设备再连接；同一端口只能由一个程序占用。

两个120Ω分别位于总线的两个物理末端；全部断电后，CANH与CANL之间应测得约60Ω。若约120Ω，通常只有一个终端生效；先核对分析仪的终端开关，再补另一端。此前故障可能与终端有关，但补齐后的实板复测尚未完成。

## 往返：USART2 → CAN → Monitor → CAN → USART2

1. CAN Monitor 连接分析仪，设1 Mbit/s，进入 **USART2 RAW / ASCII 双向测试** 页。ID填板上 ID，点击 **应用ID**，勾选 **RAW测试：收到即同ID原样回传**。
2. 关闭另一页的 **SHARK 测试帧立即回传**；该功能是性能协议的 `6E0→6E1` 回复，不用于 RAW 字节流。
3. SHARK 连接 USART2 适配器，进入 **USART2 RAW / 双向长文本** 页。先用短文本 `Hello UART2!` 测试，再生成16384字节长文本。
4. 保持默认写入块32字节、间隔5ms、超时2000ms，点击 **开始RAW双向测试**。
5. Monitor 的 ASCII 框应连续显示原文，CAN帧边界不会额外插入换行。SHARK 显示发送/返回字节数和耗时；只有全部逐字节一致、且末尾100ms没有额外字节才显示 **PASS**。错误时给出首个差异偏移或超时。
6. 点击 **导出结果 / 收发文本**，保存 JSON、`.tx.txt`、`.rx.txt`；JSON包含配置、收发 SHA-256 和原始 HEX。每次重新开始会清除串口中尚未读取的旧数据。

此测试检查正反两条路径，但反向数据由收到的正向数据触发；还需进行下一项独立反向测试。

## 独立反向：Monitor → CAN → USART2

1. 停止往返测试，关闭 Monitor 的 RAW 同 ID 回传。
2. SHARK 保持 RAW 页和串口连接，点击 **清空接收**；将发送原文框内容改为预期接收的文本，例如 `Hello from CAN Monitor!`。
3. Monitor 下方 ASCII 发送框填完全相同的文本；注意末尾换行也是数据。帧间隔先保留2ms，点击 **发送ASCII到USART2**。
4. SHARK 接收框应显示文本。发送完毕后，点击 **比对独立反向接收**，预期字节数和全部内容一致才显示 **PASS：CAN→USART2独立反向接收逐字节一致**。
5. 可粘贴更长的相同文本重复验证。再改为其他 ID 发送短文本，USART2 应没有新字节；改回板上 ID 后应恢复输出。

Monitor 每帧最多8字节，末帧用实际长度，不添加额外换行。SHARK 的独立反向缓存最多1MiB，显示最近16KiB；Monitor 显示和导出保留最近64KiB，但累计帧数/字节数覆盖全程，完整原始帧可用其 CSV 记录。非 ASCII 控制字节显示为 `\xNN`，显示转义不改变传输数据。

## 结果和边界

RAW 只按接收顺序拼接 payload，不恢复串口外设的消息边界。业务需要靠自己的固定长度、帧头/长度或校验解析；多个 CAN 发送者同时使用同一个 ID 会混合为一条字节流。测试期间停止其他同 ID 流量。

默认限速和256字节在途窗口用于先验证完整性，耗时覆盖电脑、串口适配器、双程CAN及Monitor，不是 MCU 单向延迟，也不证明最大吞吐。先通过短文本/长文本、独立反向，再逐步减小间隔；出现差异或超时应保留结果，通过 USB / USART1 的 `stats` 检查 UART errors/rx_drop/tx_drop、CAN FIFO丢帧及TEC/REC。UART无硬件流控，持续过载仍可能丢数据。

超时先看 Monitor 是否收到对应 ID 的帧：没有则检查 USART2 RX、CAN收发器供电、CANH/CANL、共地和位速率；有接收但无返回则检查 RAW回传开关、回传失败计数及 USART2 TX 接线。USB↔USART1透明模式没有协议控制入口，先按键切回 CAN 模式后再配置。

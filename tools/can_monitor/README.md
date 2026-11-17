# Linux CAN1 观察台

本目录已收进SHARK开发包，可在解压目录直接运行 `python3 tools/can_monitor/app.py`。交付包不含 `vendor/` 中的原厂SDK或本机运行库；旧CDC串口与SocketCAN后端可直接使用，原厂原生USB后端按下文准备依赖。

为 code_914 的 CAN1 裕度分析提供接收界面。支持达妙 USB2FDCAN 原生 USB 固件
（官方 DM_DeviceSDK v1.1.0）、旧版 USB2CAN CDC 串口协议、已配置的 SocketCAN，以及演示数据。

```bash
./tools/can_monitor/run.sh          # 实机界面
./tools/can_monitor/run.sh --demo   # 自动连接演示数据
./tools/can_monitor/run.sh --scan   # 扫描原生 USB 和 USB 串口
./tools/can_monitor/run.sh --connect --port /dev/ttyACM0  # 直接打开并持续等待数据
```

界面默认“自动识别”，启动即扫描原生 USB 和 USB 串口。设备序号 `0`、通道 `0`、波特率 `1000000`。
点击“连接并记录”。也可手选“达妙原厂 SDK”或“达妙串口 USB2CAN”及串口路径。
单通道设备是 **CH0**；这里的通道号
与板上的 CAN1 命名不同。停止后生成 CSV 和同名 JSON，默认在 `~/sample/can1/`。
演示文件名包含 `_DEMO`，每 6 秒切换普通轮询/批量轮询。

## 安装

界面使用 Python 标准库及 Tk；USB 串口扫描/连接额外需要 `pyserial`（本机已安装）。
不要求官方 Python 包的 Python 3.13。Ubuntu 缺 Tk 时安装 `python3-tk`，缺串口库时安装 `python3-serial`。

### 串口型号与扫描

`34b7:6877` 是 SDK 使用的原生 USB 设备。`2e88:4603` / `0484:5742` 的 USB 串口会作为
旧版协议候选自动填入串口栏；这些通用 USB 标识不能单独证明设备型号或固件兼容。
SDK 返回零个设备时仍会显示 USB 串口，SDK 未安装也不会隐藏串口。

旧协议参考达妙官方 `USB转CAN-linux/USB_CAN.py` 和 `AcTool/Form1.cs`、`can.cs`：
921600、8N1，配置 CAN 使用 `55 05 <index> AA 55`，接收包固定 16 字节。
连接会先停止分析仪原有的周期发送，再请求配置 CAN，然后持续接收。
与原厂串口程序一致，不强制等待配置应答；部分固件不回应短控制命令。
界面分别报告串口打开、实际 CAN 接收计数和配置应答状态。未收到帧时为黄色等待状态，
收到有效 CAN 帧后为绿色。没有 `CMD=0x13` 时不会把波特率标为已确认。
JSON 分别保存收到的串口字节数、实际 CAN 接收帧数、配置确认状态和最多 256 字节原始预览。
此协议只支持经典 CAN，不能用于解析 CAN FD。

2026-10-31 实机：系统检测到 `/dev/ttyACM0`，VID/PID `2e88:4603`，串号 `00000000050C`。
去掉强制配置应答的条件后，实机 3.49 秒接收并保存 **1614 帧**，ID 为 `0x123`、DLC=5、
数据 `00 0D 0A A5 FF`，应用队列丢帧为 0。设备未返回配置应答，协议接收已验证。
这次收到的是测试帧，不能作为 code_914 五个电机的总线裕度结论。
未更改或刷写分析仪固件。

SDK 是独立的官方二进制下载，保存在忽略提交的 `vendor/`，版本、Git revision、
SHA256 固定在 `setup_sdk.py`。无需刷分析仪固件。

```bash
python3 tools/can_monitor/setup_sdk.py
```

官方二进制要求 `GLIBCXX_3.4.32` 和较新的 libusb。Ubuntu 22.04 可使用以下本地方案：

```bash
# 构建需要 build-essential autoconf automake libtool
python3 tools/can_monitor/setup_sdk.py --local-libusb
```

程序会查找 `~/miniconda3/lib/libstdc++.so.6`、`~/anaconda3/lib/libstdc++.so.6`
或当前 `CONDA_PREFIX` 中的兼容 C++ 运行库；也可显式设置：

```bash
DMCAN_CXX_RUNTIME=/path/to/libstdc++.so.6 ./tools/can_monitor/run.sh
```

这些库只在当前进程加载，不替换系统库。不自动下载或安装整个 Conda。
SDK 加载错误会显示在界面。此前开发机已准备好SDK、本地libusb和兼容C++库；这些本机依赖不包含在交付ZIP中。

USB 权限不足时，可安装目录中的 `99-damiao-can.rules` 到
`/etc/udev/rules.d/`，执行 `sudo udevadm control --reload-rules` 后重新插拔。
规则仅覆盖官方单通道 VID/PID `34b7:6877`，双通道型号需先确认实际 VID/PID。
不建议以 root 身份运行整个 GUI。

## 记录内容

- 原始 ID、DLC、完整数据、通道、标准/扩展、远程、FD、ESI、ACK 标志。
- 电脑单调时钟、电脑 Unix 时间，以及 SDK 的设备原始时间戳。
- 命令 ID `01/02/03/04/06` 与反馈 ID `10`；反馈按 `Data[0]&0x0F` 拆分五个电机。
- 各路帧数、近 1 秒频率、相邻间隔、P99、全程最大间隔、距上次接收时间。
- 10/100/1000 ms 窗口估算占用（JSON/统计内部），GUI 显示 100/1000 ms。
- 应用接收队列溢出、SDK 错误事件、写盘异常；SocketCAN 额外保存内核 socket 丢包计数。
- 串口模式在 JSON 记录配置应答、适配器状态码计数、解析丢弃字节与残包；状态应答不算作 CAN 帧。

CSV 在后台逐帧写入；UI 每 250 ms 更新快照，最近帧显示的裁剪不影响日志。
串口读取按当前可用字节数处理，空闲时等第一个字节，避免等待大缓冲填满而人为增加 50 ms 延迟。
停止时先停接口，再排空日志。P99 取每组最近 10000 个非负间隔，全程最大值单独保留。
不把 application queue 无溢出表述成分析仪/USB/主控均无丢帧。

## 测量边界

本工具**默认只接收**；显式启用性能回传、RAW同ID回传或点击RAW文本发送后才发送CAN数据，设置见后文。
配置波特率、开关分析仪通道及停止分析仪周期发送属于适配器控制操作。
官方 SDK 没有公开静默模式接口，串口协议也未确认静默模式，因此原厂接口按参与 ACK 处理。

当前间隔与频率明确使用**电脑接收时间**。USB 批量上传会产生抖动，不能据此
证明线上存在某次亚毫秒延迟。SDK 时间戳的单位与来源尚未实机确认，原样保存，
不擅自按微秒换算。先用长时间帧计数和缺帧位置做第一轮分析。

占用估算对已收到的经典 CAN 成功帧重建 CRC15 和实际填充位，包含 3 位帧间隔。
不含未收到的帧、错误/过载帧及失败重发，CAN FD 帧只记录，不参与占用估算。
短窗口可受 USB 聚集上传影响，甚至超过 100%；这不代表物理总线真的超过容量。
错误回调事件不等同于主控 TEC/REC 或完整错误帧抓取。

SocketCAN 模式使用已有 `can0`，不自动改网卡配置。界面 bit/s 必须与网口实际值一致，
否则占用估算会错。若分析仪已是 gs_usb 固件，可在系统中配置 listen-only 后使用。
不需要为使用本工具而更换固件。

## SHARK 延迟测试回传

连接后勾选 **SHARK 测试帧立即回传**；默认请求ID `6E0`、回复ID `6E1`（十六进制）。
修改ID后点击“应用回传设置”，两ID必须不同。界面显示收到、已提交和失败计数；断开后关闭回传。
也可启动时指定：

```bash
./tools/can_monitor/run.sh --connect --port /dev/ttyACM0 --echo

# 不显示GUI，使用相同后端与回传逻辑；Ctrl+C保存计数并退出
python3 tools/can_monitor/echo_server.py --port /dev/ttyACM0 --output /tmp/can_echo.json
```

无GUI入口还支持 `--socketcan can0`、`--native`、`--request-id`、`--reply-id`、`--seconds`。
每次选择一种后端。串口、原厂SDK和SocketCAN均有发送实现；本次实板验证的是达妙旧CDC串口版本，
SDK和SocketCAN发送仅经模拟测试。

回传只接受请求ID匹配的**8字节、RX、标准、Classical、非远程帧**，原样复制数据到回复ID。
自己的TX回显、回复ID、错误/扩展/FD帧都不会触发回复，防止循环。
回传在接收回调中提交，先于界面刷新和日志写入；默认关闭，不修改普通接收流程。
JSON保存回传设置、收到/提交/失败计数；`max_service_us`仅为回调调用发送接口的耗时，不能视为物理CAN延迟。

旧串口发送采用官方 `AcTool/can.cs` 的30字节单次发送格式：`55 AA 1E 01` +
17字节packed CAN_Function + 8字节补零载荷 + CRC8。`sendTimes=1`、`sendInterval=1`、
`idAcc=dataAcc=0`。CRC使用反射多项式 `0x8C`、初始0，覆盖包括零CRC槽在内的全部30字节，
随后替换最后一字节。2026-10-31已实测双向回传成功。

在SHARK助手的“CAN 延迟 / 吞吐测试”页发起测试。两端CAN均设1Mbit/s，停止其他发送，
确保测试ID未被业务占用。该RTT包括两端电脑/USB与分析仪开销，不等同SHARK单向延迟；
发送接口返回成功只证明提交成功。分析仪TX报告与SHARK实际接收计数需一起检查。

## USART2 RAW / ASCII 双向测试

新增 **USART2 RAW / ASCII 双向测试** 页，用于 SHARK 专用 USART2 原始字节↔CAN 链路。连接设置与日志开关仍在上方，两端 CAN 均为1 Mbit/s。

1. 填板上 ID（十六进制，默认 `001`；若板上序号5则填 `005`，序号32填 `020`），点击 **应用ID**。接收框将匹配标准数据帧的 payload 按顺序连接，帧边界不插入空格或换行，因此长ASCII文本可连续阅读。
2. 往返测试时勾选 **RAW测试：收到即同ID原样回传**。SHARK 助手选择接到USART2的USB-TTL适配器，进入 **USART2 RAW / 双向长文本**，生成文本并开始测试；助手完整比对返回字节，全部一致才PASS。
3. 独立反向时关闭RAW回传，在本页下方填写ASCII，点击 **发送ASCII到USART2**。文本按最多8字节分帧，默认帧间隔2ms，末帧用实际长度，不添加额外换行；可粘贴长文本，最多1MiB。SHARK清空接收后，填写相同预期文本并使用 **比对独立反向接收** 检查完整性。

RAW回传允许同ID，支持1～8字节短帧；仅处理RX、标准、Classical、非远程帧，自己的TX报告不会再次回传。此开关与上方性能测试的 `6E0→6E1` 回传相互独立；RAW测试时关闭性能回传，避免重叠ID产生额外回复。回传在接收回调中立即提交，断开时关闭并停止手动发送。旧CDC后端的 `0x02` 发送失败状态会实时显示为 **适配器发送失败**；它与主机调用发送接口时的 **提交失败** 分别计数，前者覆盖本次连接的全部发送。

显示保留最近64KiB，可 **导出接收字节**，累计字节数不受显示裁剪影响；完整原始帧可开启CSV记录。非打印字节以 `\xNN` 显示。此页只拼接原始字节，不解析外设自身的消息格式；长文本一致性由SHARK助手逐字节检查，发送接口的提交计数本身不证明物理CAN传输成功。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/can_monitor/tests -v
```

官方资料：

- https://github.com/dmBots/dm-device-sdk/tree/36a8b3971dc35608701d128f353a32cad3ee6cdb
- https://github.com/dmBots/usb2canfd

未连接实机时，SDK 只能验证加载/枚举；实际固件兼容、接收完整性、拔插恢复需硬件确认。

# SHARK 数据与控制协议 v1

本节速查适用于2026-11-02开发版；后文给出完整字段定义。使用步骤见 [使用教程](user-guide.md)。

| 链路 | 外设/主机写入 | SHARK输出 |
|---|---|---|
| USART2，固定RAW | 直接写ASCII或任意二进制 | 标准CAN ID=板上ID，payload最多8B；反向匹配此ID的payload原样输出USART2 |
| USART1 / USB，`ascii` | CR/LF结尾的ASCII命令，或SHARK二进制包 | 命令回复＋ASCII CAN报告 |
| USART1 / USB，`binary` | 同样接受ASCII命令与SHARK二进制包 | 命令回复＋二进制CAN_RX报告 |
| USART1 / USB，`raw` | 原始字节 | 与USART2相同的板上ID分包与匹配返回；无带内命令 |
| `usb-ttl` 模式的USB / USART1 | 原始字节 | USB↔USART1直接转发；USART2继续独立RAW↔CAN |

### 三个最小例子

**ASCII转CAN（USB / USART1）**：发送 `send 123 48656C6C6F\r\n`，产生标准ID `0x123`、DLC=5、payload=`48 65 6C 6C 6F`（`Hello`）。`write 48656C6C6F\r\n` 使用板上ID。`id 16\r\n` 的16是十进制，对应CAN `0x010`；`send 16 ...` 的16是十六进制，对应CAN `0x016`。

**RAW转CAN（USART2）**：板上ID=5时，写入ASCII `Hello CAN!` 共10B。一种可能的分包为ID `0x005` / DLC8 / `48 65 6C 6C 6F 20 43 41`，随后同ID / DLC2 / `4E 21`；DMA/IDLE边界可能形成更短帧，接收端应按顺序拼接。反向收到同ID标准数据帧 `4F 4B 0A`，USART2仅输出 `OK\n`，不额外添加字符。扩展ID `0x005` 或其他标准ID不会输出到USART2。

**二进制转CAN（USB / USART1）**：在工程根目录运行下面代码可查看实际线格式；`wire` 可直接写入921600串口：

```python
import sys
sys.path.insert(0, 'tools')
from bridge_protocol import Packet, CAN_TX, REQUEST_ACK, encode
wire = encode(Packet(CAN_TX, b'Hello', id=0x123, sequence=1, flags=REQUEST_ACK))
print(wire.hex(' '))
```

该包为标准CAN `0x123`、5字节数据，入队成功返回同sequence的ACK。需要异步CAN接收报告时先执行 `wire binary\r\n`；否则仍按默认ASCII输出。`wire`控制报告格式，不能用来区分RAW中的业务数据类型。原生USB与USART1的协议封装不会出现在CAN payload中。

### 共同约定

- CAN只承载原始payload；STREAM/RAW都不增加CAN分片序号、业务长度或业务CRC，不保留消息边界。
- 板上ID十进制1～32，仅影响RAW、`write`、`test`及DEFAULT_ID标志；显式指定的CAN ID不受影响。
- ASCII `OK queued` 与二进制成功ACK表示软件入队；不是物理CAN对端确认。RAW没有应用层ACK或重发。
- 串口二进制CRC只覆盖SHARK串口包；CAN链路另有控制器自带CRC。业务可靠性、端到端校验和去重由业务协议负责。
- USART2始终不执行SHARK命令；配置经USB / USART1的协议入口进行。USB↔TTL透明模式先按键退出再配置。

USART1固定921600；USART2默认921600，可通过ASCII命令运行时修改。均为8数据位、无校验、1停止位、无硬件流控。USB CDC的line coding默认921600，不改变USB总线速率或UART配置。所有多字节二进制字段均为**小端**。

## 路由与端口格式

上电：工作模式 `uart-can`、CAN normal，USART1 / USB 为 `wire ascii`，USART2 固定 `wire raw`；ID 从 Flash 读取，无有效记录则为1。

`uart-can` 和 `usb-can` 均允许 USART1 / USART2 / USB 同时提交 CAN，区别是先处理 UART 或 USB，以及 LED 指示。每轮各处理最多256输入字节，CAN RX 最多16帧，再处理 UI / 输出；不让单个输入端口独占转发任务。CAN RX 广播到协议端口，RAW 端口只输出匹配板上 ID 的标准数据帧 payload。

`usb-ttl` 将 USB 与 USART1 直接互转，不识别文本、转义或二进制帧；USART2 保持原始字节↔CAN。此模式没有协议控制入口，请按键切回 CAN 模式后配置。USB / USART1 不混入 CAN 报告或日志。切换模式、ID、CAN状态或端口格式时先停业务流量：重新初始化 CAN 会取消未发队列；切换工作模式 / wire 会清理相应旧输出队列，已启动的 DMA / USB 传输仍会完成。

USART1 / USB 的 `wire` 可独立设置；USART2 仅支持 `raw`：

| 值 | 输入 | CAN RX 输出 |
|---|---|---|
| `ascii` | ASCII + 二进制控制帧 | `CAN S/X ...` 文本 |
| `binary` | ASCII + 二进制控制帧 | 二进制 CAN_RX |
| `raw` | 原始字节立即分包，最多8字节/帧，标准 CAN ID=板上ID | 仅返回标准帧且 ID=板上ID的原始数据 |

`wire` 不影响控制回复格式：ASCII 指令回复文本，二进制指令回复二进制。RAW 和透明模式没有带内控制指令或逃逸序列；通过另一路协议端口修改，例如 USB 上执行 `wire uart1 ascii`。若 USART1 / USB 也都配置为 raw，可重启恢复默认；按键切换工作模式不会重置 wire。

`baud uart2` 仅能从USART1（板载CH340）或USB CDC的ASCII命令入口执行；USB↔USART1透明模式下先按键退出。允许值为9600、19200、38400、57600、115200、230400、460800、921600、1000000、1500000、2000000、3000000。新速率仅在本次上电期间生效，复位后恢复921600；该命令不改变发送命令的CH340或USB串口参数。切换时会停止并重启USART2 DMA，队列排空后才回复，切换边界上的串口数据可能丢弃。收到成功回复后立即将USART2外接设备改为新速率；不要通过USART2本身配置，因为USART2所有字节始终作为RAW数据转CAN。`stats` 会显示 `uart2_baud`。

USART2 的所有输入均为数据，包括 `ping\r\n`、`mode ...`、零字节和二进制控制帧。输入立即拆为 1～8 字节标准 CAN 帧，ID 为板上 ID；反向只接受匹配 ID 的标准数据帧并输出原始 payload。不会补齐到8字节，不添加换行，不发送启动或按键日志。经 USB / USART1 执行 `wire uart2 ascii` 或 `wire uart2 binary` 返回错误；`wire uart2 raw` 允许，但通常无需执行。旧 `uart2 TEXT` 命令已移除。

原始流按接收顺序拼接，不保留原始发送调用/消息边界，不保证每个 CAN 帧均为8字节；过载仍可能丢字节。外设自身的帧头、长度或校验作为 payload 保留，由业务端解析。若可控制 USART1 / USB 的发送协议并需要显式 ID、长度、校验与错误反馈，使用以下二进制协议。双向 ASCII 长文本验证见 [测试指南](uart2-raw-test.md)。

## ASCII 指令

CR、LF 或 CRLF 结尾，最多191字节（不含行结束符）。HEX 可连续或以空格分隔，单个 CAN 最多8数据字节，`-` 表示空帧。ID命令是十进制，发送指令的显式ID是十六进制。

| 命令 | 回复与行为 |
|---|---|
| `help` | 指令列表 |
| `ping` | `pong` |
| `echo TEXT` | 原样返回 TEXT 与 CRLF |
| `stats` | 工作模式 / can_state / ID / 队列 / 错误与端口统计，多行 |
| `mode uart-can` / `mode usb-can` / `mode usb-ttl` | 切换工作模式；`OK mode=...` |
| `mode id` | 进入按键 ID 设置，不改变工作模式 |
| `can normal` / `can loopback` / `can standby` | CAN诊断状态0/1/2；`OK can` |
| `send 123 1122334455667788` | 标准 CAN ID=0x123 |
| `sendx 1ABCDEFF 00112233` | 扩展 CAN ID=0x1ABCDEFF |
| `send 0 -` | 标准ID0、零长度数据帧 |
| `write 00112233` | 使用板上标准ID |
| `test` | 使用板上ID，数据 `55AA000102030405` |
| `id` | `id=7 hex=007 saved_id=7` 等 |
| `id 7` | 运行ID改为7；`OK id; save to persist`，不保存 |
| `save` | 保存当前运行ID；`OK saved` 或 `ERR flash save` |
| `id cancel` | 取消按键设置，保留运行ID |
| `baud uart2` | 查询USART2当前波特率 |
| `baud uart2 460800` | 运行时将USART2改为460800；成功后立刻调整外接串口设备 |
| `wire binary` | 改当前端口；`OK wire` |
| `wire uart1 raw` | 改指定端口；uart1 / usb 支持三种格式，uart2 仅允许 raw |
| `leds F` / `leds 0` / `leds auto` | 四灯手动掩码或恢复自动，bit0..3依次LED1/2/3/SIGNED |

兼容旧测试命令 `mode bridge/loopback/standby`：分别等价 CAN normal/loopback/standby，并回复 `OK mode=...`；不改变三个工作模式。

ASCII `OK queued` 表示已进入**软件发送队列**，不是 CAN 已成功发出。物理 CAN 成功发送需对端 ACK，观察 `can_tx_sent` 和 TEC / busoff。待机、队列满、bus-off 或非法帧返回 `ERR`。修复物理总线后用 `can normal` 重新初始化恢复。

ASCII CAN RX 报告（CRLF结尾）：

```text
CAN S 00000123 8 55AA000102030405
CAN X 1ABCDEFF 4 00112233
```

## 二进制线格式

```text
00 A5 COBS(原始包) 00
```

每一帧均带完整 `00 A5` 前缀。COBS 把原始包中的零编码掉；数据中的 `00 / A5 / CR / LF / FF` 都可使用，不会变成 ASCII 指令。接收器按字节流解析，支持任意拆包、连续粘包；遇到新零分隔符可重新同步。非法长度 / COBS / CRC 不执行命令、不回复，计入 parse_err；主机等待超时后可重发。

原始包：

| 偏移 | 字节数 | 内容 |
|---:|---:|---|
| 0 | 1 | 版本，固定1 |
| 1 | 1 | 类型 |
| 2 | 1 | flags |
| 3 | 1 | payload长度，0～64 |
| 4 | 2 | sequence，主机自增16位，回复保持相同序号 |
| 6 | 4 | CAN ID（非CAN指令通常填0） |
| 10 | N | payload |
| 10+N | 2 | CRC-16/CCITT-FALSE，小端 |

CRC 覆盖版本到 payload 的全部字节，poly=`0x1021`、init=`0xFFFF`、无反射、xorout=0；`123456789`→`0x29B1`。原始包12+N字节，COBS后加前后标识共 **16+N** 字节，最大80。

flags 对 CAN_TX / STREAM_TX：

| 位 | 值 | 意义 |
|---:|---:|---|
| 0 | 0x01 | 扩展ID；清零为标准ID |
| 1 | 0x02 | 请求成功入队 ACK |
| 2 | 0x04 | 使用板上运行ID，忽略包内ID |
| 3～7 | — | 保留，必须0 |

板上ID始终1～32；若同时设置 EXT，则用相同数值发送扩展帧。CAN_RX只设置EXT标志。ECHO原样保留ID、flags、sequence并回传payload；其余控制命令flags通常填0。

## 二进制类型与回复

| 类型 | 名称 | 请求 payload / 语义 |
|---:|---|---|
| 0x01 | CAN_TX | 0～8字节，一帧 Classical CAN |
| 0x02 | STREAM_TX | 0～64字节，顺序拆成≤8字节的 CAN 帧；空payload发一个空帧 |
| 0x10 | ECHO | 任意0～64字节，返回ECHO_REPLY |
| 0x20 | INFO | 无payload，返回INFO_REPLY |
| 0x21 | MODE | 1字节：0 uart-can / 1 usb-can / 2 usb-ttl |
| 0x22 | ID | 1字节：十进制1～32，修改运行值，不保存 |
| 0x23 | SAVE | 无payload，保存当前运行ID |
| 0x24 | CAN_TEST | 1字节：0 normal / 1内部回环 / 2待机 |
| 0x81 | ACK | payload为 `[请求类型, 状态]` |
| 0x82 | CAN_RX | id / EXT / payload为接收的CAN内容；sequence=0，无发送请求关联关系 |
| 0x90 | ECHO_REPLY | 回传ECHO请求 |
| 0xA0 | INFO_REPLY | 以下9字节状态 |

ACK 状态：0成功，1参数/类型非法，2待机/队列忙/bus-off，3Flash保存失败。CAN_TX / STREAM_TX 的成功ACK仅在请求ACK标志时回复；失败始终回复ACK。MODE / ID / SAVE / CAN_TEST始终回复ACK。INFO / ECHO分别使用专用回复。不能将 ACK 当作总线对端接收确认。

INFO_REPLY payload：

| 索引 | 内容 |
|---:|---|
| 0 | 工作模式0～2 |
| 1 | 运行ID |
| 2 | 已保存ID（无有效记录时默认1） |
| 3 | CAN状态0 normal / 1回环 / 2待机 |
| 4 | 是否处于按键ID设置 |
| 5 / 6 / 7 | USART1 / USART2 / USB 的wire：0 ASCII / 1二进制 / 2原始流 |
| 8 | 当前请求入口：0 USART1 / 2 USB；1为线格式保留值，当前USART2固定RAW，不接受INFO |

STREAM_TX 在提交前检查软件队列空间，整批入队或整批拒绝。CAN 总线上仍是独立 Classical CAN 帧，**不添加分片序号、长度或CRC到CAN数据域**；原串口sequence仅用于串口控制回复。接收端如需还原业务消息，应在业务payload中定义消息头/长度或使用已知固定长度。不要在收到ACK超时后无条件重发非幂等业务：设备不做sequence去重，重发可能导致重复CAN帧。推荐实时控制发送带时间戳/业务序号的最新状态，并在应用层处理过期与重复。

固定编码示例：ECHO、flags=2、sequence=0x1234、id=0x123、payload=`00 0D 0A A5 FF`：

```text
00 A5 09 01 10 02 05 34 12 23 01 01 01 07 0D 0A A5 FF E4 89 00
```

Python 参考编码与同步客户端在 `tools/bridge_protocol.py`，无需 pyserial 即可使用纯编码函数：

```python
from bridge_protocol import Packet, CAN_TX, REQUEST_ACK, DEFAULT_ID, encode
wire = encode(Packet(CAN_TX, b'\x00\x0D\x0A\xFF', sequence=1,
                     flags=REQUEST_ACK | DEFAULT_ID))
# port.write(wire)
```

## 流控、错误与测量

软件CAN TX队列可用31帧，CAN RX可用63帧；每端口环可用1023字节。USB有OUT背压；UART没有RTS/CTS，连续输入过量会丢失。协议端口接收溢出后解析器丢弃受损文本直到行结束或二进制前缀恢复；RAW 端口保留已缓存字节并累计 `rx_drop`，不产生协议解析错误。CAN报告或RAW返回因输出拥堵会丢整条记录/帧payload；这些路径都应通过 USB / USART1 的 `stats` 检查。

`can_tx_queued` 为累计软件入队，`can_tx_sent` 为控制器完成发送的累计帧数；`can_pending` 仅为软件待发送帧数，不含硬件FIFO。重配CAN会清空未完成队列，累计计数不清零。`rx_drop` / `tx_drop` 是字节数；`can_drop` / `fifo_lost` 是接收帧丢失计数。`stack` 的单位为32位字，不是字节。统计计数32位可回绕。`dispatch_us_max` 是回调到任务的近似最大等待，DWT周期计数在144MHz约29.8秒回绕，仅供调度诊断。

低延迟测试使用 `serial_assistant.py --benchmark`。输出包括回显RTT的p50/p95/p99/max，以及窗口8的每方向有效payload吞吐；不是CAN单向线延迟。`--bridge-test` 测透明双向随机字节，包含主机调度及调用开销。建议 Release、Linux串口低延迟配置按驱动实际支持情况评估；以逻辑分析仪的UART/CAN边沿测量作为单向延迟依据。

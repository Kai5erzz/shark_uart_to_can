# SHARK 使用教程

整理日期：2026-11-02。适用于本包的 STM32G431CBT6 / TJA1044 开发固件。

Release已在开发板烧录校验，16KiB内部CAN回环通过。外部CAN短文本曾通过，但长文本和后续反向失败；总线终端可能只有一端，补齐后的复测尚未进行。请先按下文接线，再完成外部双向验证。[实板记录](uart2-raw-20261031.md)保留了实际结果。

## 1. 选择接口

| 需求 | 使用接口 | 发送内容 |
|---|---|---|
| 外设已有串口协议，直接转CAN | USART2，PA2 TX / PA3 RX | 原始字节，ASCII和二进制均可，无需SHARK封装 |
| 电脑通过USB转CAN | 原生USB CDC，PA11 / PA12 | SHARK ASCII或二进制协议；也可配置RAW |
| CH340或串口主机转CAN | USART1，PA9 TX / PA10 RX | SHARK ASCII或二进制协议；也可配置RAW |
| 电脑USB转TTL | 原生USB与USART1，切换到 `usb-ttl` | 两边原始字节直接互转 |

USART2始终为RAW↔CAN，三种工作模式下都可用。USART1 / USB上电默认是协议入口；助手的“透明/原始流”复选框只控制助手行为，不会改变板上模式。

## 2. 接线与终端

USART1固定 **921600、8数据位、无校验、1停止位、无流控**；USART2默认同为921600，支持后文的运行时波特率设置。使用3.3V TTL信号。USB CDC line coding默认921600，实际USB吞吐由USB链路决定。CAN固定 **Classical CAN 1 Mbit/s**，不支持CAN FD或远程帧。

| 功能 | 接法或电平 |
|---|---|
| 外部USB-TTL接USART2 | 适配器TX→PA3，适配器RX←PA2，GND共地 |
| USART1 | PA9 TX→对端RX，PA10 RX←对端TX；板载CH340使用这一路 |
| TJA1044数字端 | PB9→TXD，PB8←RXD，PB7→STB；STB低为正常工作 |
| CAN总线 | CANH↔CANH，CANL↔CANL，GND共地 |
| 收发器供电 | TJA1044 VCC约5V，按具体后缀/板级设计连接逻辑电源 |
| 按键 | PA0，低有效 |
| LED1 / LED2 / LED3 / SIGNED | PA5 / PB0 / PB10 / PB3，全部低有效 |
| DAPLink | SWDIO→PA13、SWCLK→PA14、GND、目标电压参考；NRST可选 |

CAN总线两个物理末端**各接一个120Ω**，其他节点用短支线连接。分析仪自带的终端可能有开关，应先确认，避免重复接入。全部断电后测CANH与CANL：两端各120Ω时并联约 **60Ω**；约120Ω通常表示只有一端终端生效。在1 Mbit/s下终端缺失可能造成反射与间歇失败。

DAPLink的USB串口与SWD下载是两条路径；USART2测试必须额外接TX/RX。原生CDC、CH340、DAPLink和CAN分析仪都可能随插拔换号，不能仅凭端口编号识别用途。

## 3. 解压、编译和烧录

VS Code打开包含根 `CMakeLists.txt` 的整个目录。编译需要ARM GCC、CMake≥3.22、Ninja；烧录需要OpenOCD，调试另需Cortex-Debug和GDB。包内已包含使用到的HAL、CMSIS、FreeRTOS和USB源码，**普通编译不需要完整Cube仓库或CubeMX**。

```bash
cmake --preset Release
cmake --build --preset Release
```

Debug将两处 `Release` 改为 `Debug`。输出在 `build/Release/uart_to_can/` 或 `build/Debug/uart_to_can/`。包内已有ELF、HEX、BIN、map，可直接使用或重新编译。

VS Code：**Ctrl+Shift+B**构建Debug；**Build Release**构建Release；**Flash Release (DAPLink)**先构建后下载校验；**Repair Boot Option (DAPLink)** 在启动选项回退时修复；F5使用 **Debug STM32G431 (DAPLink)**。**Serial Assistant** / **CAN Monitor** 启动两端工具，**Package Project** 重新打包。命令行：

```bash
openocd -f openocd/daplink_stm32g4.cfg \
  -c 'shark_program {build/Release/uart_to_can/uart_to_can.elf}'
```

保持本板已实测的 `shark_program` 复位流程。应用限制为前60KiB，ID保存在 `0x0800F000～0x0800FFFF`；正常任务烧录保留ID，全片擦除会清除。本包不包含开发板Flash中的ID日志。

`shark_program` 开始前会读 `FLASH_OPTR`；若提示 `FLASH_OPTR=... selects the system bootloader`（复位停在 `0x1FFFxxxx`），先执行一次修复再烧录：

```bash
openocd -f openocd/daplink_stm32g4.cfg -c shark_fix_boot
```

该命令只写 `nBOOT0`（bit 27，掩码 `0x08000000`）并重载选项字节，RDP、BOR、WRP、PCROP 等其他位保持不变；已修好时只读检查。2026-11-06 的处理记录见 [验证记录](validation.md)。

重新生成底层代码才需要STM32CubeMX6.18.1和STM32CubeG4 V1.6.3。IOC为 `uart_to_can/uart_to_can.ioc`，推荐执行 `python3 tools/regenerate.py --cubemx <启动器或JAR路径>`，脚本恢复60KiB链接限制。固件包来源与校验见 [firmware_packages.json](firmware_packages.json)；原下载仓库位于D盘，普通编译不依赖该绝对路径。

## 4. 启动上位机与基础通信

推荐Linux、Python≥3.10、Tk、pyserial。CAN Monitor的硬件路径在Linux验证；SHARK助手可在带Tk的Windows Python运行，端口改为COM编号。

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/serial_assistant.py --list
python3 tools/serial_assistant.py
# 同一工程目录，另开终端
python3 tools/can_monitor/app.py
```

Ubuntu缺Tk可安装 `python3-tk`。Monitor选择“达妙串口USB2CAN”、实际分析仪串口、CH0、1000000 bit/s。旧CDC后端不需要原厂SDK；原生USB后端按其 [README](../tools/can_monitor/README.md) 单独安装SDK，本包不附本机SDK二进制/运行库。

先通过原生USB或USART1连接SHARK，文本栏勾选CRLF，依次发送：

```text
ping
id
stats
can normal
send 123 48656C6C6F
```

`ping`返回 `pong`，`id`显示运行/保存值；`send`提交ID `0x123`、5字节 `Hello`。Monitor应看到 `48 65 6C 6C 6F`。`OK queued`仅表示入队，同时检查Monitor和 `can_tx_sent`。

可通过当前CH340/USART1或USB CDC配置USART2速率，不影响命令入口本身：先发 `baud uart2` 查询，再发例如 `baud uart2 460800`。成功回复后立即把连接USART2的USB-TTL/外设改为460800。可选值为9600、19200、38400、57600、115200、230400、460800、921600、1000000、1500000、2000000、3000000；只在运行期间有效，重启恢复921600。不要从USART2发送设置命令，它是纯RAW数据口；若USB↔USART1处于 `usb-ttl` 透明模式，先用按键切回CAN模式再配置。详见[波特率命令](protocol.md#ascii-指令)。

USART2不执行以上命令；写入 `ping\r\n` 会作为6字节数据发到CAN。完整报文规则见 [通信协议](protocol.md)。

## 5. 模式、按键与ID

| 模式 | 数据路径 | 指示 |
|---|---|---|
| `uart-can`，上电默认 | 三路提交CAN，优先UART | LED1亮，SIGNED 1Hz |
| `usb-can` | 三路提交CAN，优先USB | LED2亮，SIGNED 0.5Hz |
| `usb-ttl` | 原生USB↔USART1；USART2仍RAW↔CAN | LED3亮，SIGNED约2秒周期呼吸 |
| ID设置临时状态 | 保持原路径 | 三颗模式灯按候选ID次数闪，SIGNED 0.2Hz |

普通状态按住 **1～不足3秒后松开**循环切模式；按住 **至少3秒后松开**进入ID设置。设置状态点按N次选择ID N（1～32，超过32回到1），按住至少1秒后松开保存退出；未点按就长按为取消，30秒无操作也取消。

序号是十进制：1=`0x001`，5=`0x005`，16=`0x010`，32=`0x020`。开发板上次保存为5，新板无记录默认1；正常烧录保留旧值。CAN模式经USB / USART1发送 `id 5` 修改运行值，随后 `save` 保存。仅ID保存，工作模式和wire重启恢复默认。

`usb-ttl` 中USB和USART1都不执行命令，退出用按键，USART2也不是配置入口。保存ID、切模式/wire或重置CAN前暂停业务，避免队列清理或Flash写入影响转发。

## 6. USART2双向RAW测试

两端使用实际板上ID，此开发板上次为 `005`，不是默认示例 `001`。

**往返**：Monitor进入 **USART2 RAW / ASCII 双向测试**，开启 **RAW测试：收到即同ID原样回传**，关闭另一页性能回传。SHARK连接实际接到USART2的适配器，进入 **USART2 RAW / 双向长文本**。先测短文本，再生成16384字节开始。Monitor连续显示原文，SHARK逐字节一致才PASS；可导出JSON和收发原文。默认32B/5ms、256B在途窗口用于完整性检查，尚未证明外部链路在该节拍稳定。

**独立反向**：关闭Monitor RAW回传；SHARK点击 **清空接收**，原文框填预期ASCII；Monitor下方填完全相同文本，点击 **发送ASCII到USART2**。收齐后SHARK点击 **比对独立反向接收** 核对PASS。末尾换行也必须一致。

CAN帧间不添加换行或协议头，串口消息边界不保留；业务端用自己的帧头/长度/校验解析，同ID不要混入其他发送者。完整步骤见 [RAW测试指南](uart2-raw-test.md)。Monitor的“提交失败”和“适配器发送失败”分别表示调用失败和旧CDC适配器报告的发送失败，提交成功不等于线上成功。

## 7. 定时发送和其他测试

- **定时发送**：选择文本/HEX或二进制CAN/回显，填间隔和次数（0持续），启动后立即发第一条；停止/断开结束。RAW文本关闭附加换行。
- **协议回环**：USB / USART1运行 `python3 tools/serial_assistant.py --port <端口> --binary-test --can-loopback`。内部回环不验证收发器/总线。
- **USB↔USART1双向**：先切 `usb-ttl`，连接原生USB与CH340，再运行 `python3 tools/serial_assistant.py --port <原生USB> --peer <USART1> --bridge-test --rounds 1000 --size 256`。
- **外部性能**：Monitor开启 `6E0→6E1` 性能回传，SHARK用USB / USART1进入 **CAN 延迟 / 吞吐测试**，先低载RTT再逐步加负载，先关闭RAW回传。RTT包含电脑和分析仪，不能除以2当作MCU单向延迟。
- **源码回归**：Linux安装GCC和Xvfb后执行 `xvfb-run -a ./tools/test_host.sh` 和 `xvfb-run -a python3 -m unittest discover -s tools/can_monitor/tests -v`。PTY/HAL替身不等同实板。

## 8. 故障排查

| 现象 | 排查 |
|---|---|
| `ping`不回复 | 确认USB / USART1、CAN模式、921600 8N1、CRLF；USART2/透明模式不执行命令 |
| Monitor无CAN | 检查TJA1044供电、STB低、PB8/PB9、CANH/CANL、共地、两端终端和1Mbit/s |
| RAW有接收无返回 | ID匹配、RAW回传开关、USART2 TX接线，查看适配器发送失败计数 |
| 短包通、长文本丢字节 | 先确认断电约60Ω和稳定单帧，再查 `stats` 丢包/错误；减小负载并导出结果 |
| bus-off不恢复 | 修复物理链路后经USB / USART1执行 `can normal` 或复位；当前无自动bus-off恢复 |
| 串口占用 | 关闭另一个助手/终端，同一设备不要被两个进程占用 |
| 断电后配置恢复 | ID需要 `save` 或按键确认；工作模式/wire不保存 |

UART没有RTS/CTS，其输入上限不等于CAN有效吞吐，不能承诺持续过载零丢包。终端修正后的外部复测、三路压力、Flash真实断电与USB重插等待验证，详见 [验证记录](validation.md)。

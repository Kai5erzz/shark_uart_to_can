# 验证记录

日期：2026-11-06（Asia/Shanghai）。当前是 UART / USB / CAN 产品开发版，历史实板记录与当前版本验证分开列出。

## 2026-11-06 启动选项回退与恢复

现象：VS Code `Flash (DAPLink)` 再次失败，日志为 `Error: timed out while waiting for target halted`，随后 `SHARK programming failed: Reset entry was not reached: .`，OpenOCD 退出码 1。

- 只读检查：`FLASH_OPTR=0xF3EFF8AA`（`nSWBOOT0=0, nBOOT0=0`），补码 `0x0C100755` 有效；`PC=0x1FFF44EA`、`VTOR=0x1FFF0000`，即复位进入系统 Bootloader，`0x08000000` 的复位入口不会被取指。Flash 内仍是 2026-11-02 的 Release 镜像（`SP=0x20008000`、复位入口 `0x08009979`），说明该镜像烧录时启动选项仍正确，之后选项字节在未重新烧录的情况下回到默认值。
- 本次芯片读回 `DBGMCU_IDCODE=0x20036468`，OpenOCD 识别为 STM32G43/G44xx、128 KiB、single-bank。10 月 31 日日志读到的 `0x20036469`（G47/G48、dual-bank）与 128 KiB 容量矛盾，当次 ID 读取可能不可靠；后续以 `0x468` 为准。启动选项回退原因尚未确定，补码有效说明不是选项字节半写损坏；若再次出现“仅上电后回退”，需怀疑选项字节掉电保持或芯片来源。
- 修复：`stm32l4x option_write 0 0x20 0x08000000 0x08000000` 只置 `nBOOT0`（bit 27），再 `stm32l4x option_load 0`。回读 `FLASH_OPTR=0xFBEFF8AA`（RDP 仍为 `0xAA`，其余位不变），复位后 `PC=0x080078C6`，原 Release 应用从 Flash 运行。
- 随后用现有 `shark_program` 烧录当前 Debug 镜像：写入 58,416 B、校验 58,412 B；复位后 `PC=0x08008BE4`，Flash 向量 `0x0800AEDD`，新镜像运行。日志 `docs/daplink_flash_20261106.log`。
- 工具修改：`openocd/daplink_stm32g4.cfg` 在复位入口断点前预读 `FLASH_OPTR`，启动选项错误时立即报出可执行的修复命令；新增 `shark_fix_boot`；`wait_halt` 超时的空错误信息改为读取 PC/VTOR 后给出系统 Bootloader 诊断；VS Code 增加 **Repair Boot Option (DAPLink)** 任务。
- 已实测完整恢复流程：故意把选项写回 `0xF3EFF8AA`，`shark_program` 立即报 `FLASH_OPTR=0xf3eff8aa selects the system bootloader ...`；`shark_fix_boot` 一次修复后再烧录成功。`option_load` 后偶见一次 `Error: Failed to write memory at 0x00000000`，是 OBL_LAUNCH 复位后探针重新枚举的已知噪声，命令仍以退出码 0 结束；修复后建议断电再上电确认选项保持。

## USART2运行时波特率设置（2026-11-02）

- 新增ASCII指令 `baud uart2` 查询及 `baud uart2 <rate>` 设置；USART1/CH340与USB CDC均可配置USART2，USART2本身保持不解析命令的RAW口。允许12档速率，默认921600，设置不写Flash、重启恢复默认。
- 波特率切换前等待UART2 TX和应用队列空闲；屏蔽USART2及其DMA IRQ，停止DMA后统计已到达DMA但未入队的数据，清理外设与NVIC挂起中断，再重配并重启循环DMA RX。切换完成回复仍从原命令入口发出。切换边界上的在途输入数据可能丢弃，主机收到回复后应同步更改外设波特率。
- HAL替身测试覆盖查询、CH340/USB入口设置、队列排空、DMA残留统计、重复设置、非法速率、切换待处理拒绝，以及USART2 RAW命令字节不解析。完整主机回归通过：3个C测试、SHARK25项Python和CAN Monitor31项Python；Python编译检查及9个Markdown文件的相对链接检查通过。
- Debug / Release构建通过；Flash分别为58412 B / 51356 B，RAM均为28512 B。ELF/HEX/BIN/map文件SHA-256已更新至 `build_artifacts.json`。本次Release BIN SHA-256为 `35103c5724dfbc97bb3f8e049283439b1ef5cc7c445cb56cf3d831a05e3288e8`。
- 本次未烧录或实板测试；串口时钟分频误差、设备端切速时序和高负载丢包仍需硬件验证。

## 2026-11-01 交付包验证

整理 [使用教程](user-guide.md) 与协议速查，将CAN Monitor源码收进本工程，并生成开发包及SHA-256清单。固件源码和二进制没有改变。在独立目录解压后重新配置/编译Release成功，BIN/HEX与原镜像逐字节一致；3个C测试程序、SHARK25项Python、Monitor31项Python全部通过。日志为 `build/package-clean-*.log`；打包内容见 [交付说明](package-notes.md)。用户提出总线可能只有一端120Ω，已加入两端各120Ω、断电约60Ω的教程；该电气修改未复测，当前RAW外部验收仍未通过。

## USART2 RAW 双向透传增补

- USART2 上电固定 RAW，三种模式均为原始字节↔CAN；每帧最多8字节，短包立即发，以板上 ID 发出；反向仅输出匹配 ID 的标准数据帧 payload。文本和二进制命令字节均不执行，不添加日志/换行。USART1 / USB 的 ASCII 和二进制控制继续保留。
- SHARK 新增 RAW 长文本页：生成/编辑 ASCII、往返逐字节校验、差异偏移、计数、SHA-256、JSON及收发原文导出，以及独立 CAN→USART2 接收比对。CAN Monitor 新增 RAW ASCII 页、同 ID 短帧原样回传和独立反向文本发送。
- `test_app.c` 直接编译实际应用路由代码并用 HAL 替身运行：256种字节、三种模式、ID1/32、任意分片、短包、反向ID/扩展过滤、队列满/待机/bus-off、溢出、无控制执行及无启动/按键日志注入均通过 ASan/UBSan。
- 最新完整主机回归通过：3个 C 测试程序、SHARK **25项** Python 测试、Monitor **31项**测试；日志为 `build/uart2-raw-host-tests.log` 和 `build/uart2-raw-monitor-tests.log`。两端真实 Tk 界面经 PTY 模拟桥完成4096字节往返、1024字节独立反向及断开取消；此测试不等同实板。
- Debug / Release 已构建；ELF/HEX/BIN/map 的当前大小和 SHA-256 已更新到 `build_artifacts.json`。实板记录见后续测试结果，步骤见 [双向透传测试指南](uart2-raw-test.md)。
- 后续接入DAPLink并烧录当前Release，写入/校验49880 B成功；板上保存ID为 `0x005`。USART2内部CAN回环16384/16384 B一致；外部链路重新插接后一项30B短文本往返成功，随后长文本出现丢字节、适配器发送失败，独立反向及单帧重测也失败。**当前外部双向透传未通过验收**，完整证据见 [RAW实板记录](uart2-raw-20261031.md)。Monitor已增加适配器 `0x02` 发送失败状态的实时显示。

## CAN Monitor性能测试历史增补（USART2 RAW 改动前）

后续已接回SHARK原生USB `/dev/ttyACM1` 和达妙旧CDC分析仪 `/dev/ttyACM0`，完成外部1Mbit/s CAN往返实测；本轮仅修改上位机工具，没有重新烧录固件。当前运行镜像的构建类型/指纹未确认。

- 助手新增性能页：低载RTT、窗口吞吐、8字节串口回显基线、分位数/抖动/超时/拒绝/重复/迟到、曲线和JSON/CSV导出。
- CAN Monitor新增显式启用的6E0→6E1立即回传；在接收回调中提交，过滤TX/错误/远程/扩展/FD及回复ID。原默认只收行为保留。另提供无GUI `echo_server.py`。
- USB入口外部CAN低载1000/1000成功，平均1.450ms、P99 1.861ms；0.75ms间隔/窗口8的10000次测试全部成功，约1017次有效往返/秒。更高突发负载出现超时，结果与两端计数见[性能报告](performance-20261031.md)。这些是整条主机RTT，不能当作单向固件延迟。
- 两批共42090个含预热的CAN请求全部到达Monitor；回复全部提交，SHARK在部分高负载项目的CAN_RX计数少于请求数。USB报告出口没有新增丢弃，故进一步定位需抓取回复总线与控制器接收事件。
- 主机回复解码上限改为1023字节，修复固件长 `stats` 回复被原191字节命令限制丢弃的问题；固件限制未变。
- C主机ASan/UBSan测试、SHARK 21项Python测试与Monitor 27项测试通过；包括真实Tk+PTY的性能完成/取消/断开/reader交接，以及Monitor回传界面启用/关闭。SDK/SocketCAN发送为模拟测试，实板验证的是旧CDC版本。

以下早期验证记录保留。上面的性能数值属于 USART2 RAW 改动前的固件，不能作为当前版本的硬件验收。

## 当前版本已验证

- STM32CubeMX 6.18.1 / STM32CubeG4 V1.6.3 实际再次生成，日志 `build/cubemx-product.log`；RTOS入口、USB收发/重置钩子、故障处理等USER CODE保留。
- IOC及生成文件：HSE32MHz、CPU144MHz、USB/FDCAN48MHz、CAN1Mbit/s、UART1/2 921600 8N1、四路低有效PWM。FreeRTOS转发任务High优先级，数据IRQ均为5，HAL Tick为TIM6。
- USB接收回调交给任务重挂接，以便接收缓冲不足时NAK；USB发送完成与UART/CAN中断使用任务通知唤醒。UART RX为64字节循环DMA，HT/TC/IDLE回调读取实时DMA游标，避免延迟HT事件把已处理字节重复视作回绕。
- Debug与Release构建成功；应用模块使用 `-Wall -Wextra -Werror`，最终构建无警告；ELF/HEX/BIN/map均已生成。
- 链接限制为60KiB，保留 `0x0800F000..0x0800FFFF` 两页日志；重生成脚本自动恢复这一限制。BIN向量表检查：MSP=`0x20008000`，复位入口位于应用Flash且带Thumb标志。
- C主机测试在AddressSanitizer / UndefinedBehaviorSanitizer下通过：原ASCII协议边界；3000组二进制往返；所有长度0..64；逐bit损坏拒绝；随机噪声/超长/坏COBS/粘包与恢复；环形缓冲随机读写/回绕；按键消抖、1/3秒边界、32计数回绕、超时和tick回绕。
- Flash软件模型测试通过：无记录默认1、ID范围、相同ID不重复写；记录每个字节位置断写后的旧ID恢复与重试；写入两个双字之间掉电；满页轮换、擦除失败、CRC损坏、1000次保存及32位记录序号回绕。该测试验证日志算法，不等同真实芯片掉电测试。
- Python 9项测试通过：1024组C/Python跨语言逐字节编码一致、固定线格式向量、流式解码恢复；真实CLI经PTY执行ASCII/二进制自测、标准/扩展/空帧/64字节分包、错误回显检测、恢复原CAN状态与wire、RTT/吞吐命令、双端口双向随机透明传输。PTY模拟设备，不运行目标固件。
- GUI在Xvfb中完成创建/关闭测试；Python语法检查、VSCode JSON与构建预设解析通过。GUI增加二进制CAN/回显、模式/ID/wire控制和原始流选项。

## 当前固件占用

| 构建 | Flash / 60KiB应用区 | RAM / 32KiB（含链接预留） |
|---|---:|---:|
| Debug (`-Og -g3`) | 58,412 B（95.07%） | 28,512 B（87.01%） |
| Release (`-Os`) | 51,356 B（83.59%） | 28,512 B（87.01%） |

Flash数字为BIN长度/链接内存占用，包含对齐。FreeRTOS 8192字节堆已计入静态RAM，应用任务3072字节栈从该堆分配，MSP预留2048字节。应用任务加解析/printf的运行栈余量尚待新版实测，用 `stats` 的stack（32位字）与heap（字节）检查。完整镜像大小和SHA-256见 `build_artifacts.json`。

## 当前尚未完成的实板验证

当前RAW版已通过DAPLink烧录并完成USART2内部CAN回环；外部链路不稳定，本轮双向透传未通过。此前USB性能为旧固件基线；Flash掉电、三路同时业务及下列完整验收仍待完成。

重新连接后按README顺序验证：

1. 使用 **Flash Release (DAPLink)** 下载/校验/复位，观察启动模式及低有效LED。
2. 分别对USART1(CH340)、USB运行ASCII和二进制自测；USART2使用RAW双向长文本及独立反向测试，不向其发送SHARK控制协议；通过USB / USART1检查 `stats` 的errors/rx_drop/tx_drop/parse_err。
3. 在两种CAN工作模式下，USART1 / USB按协议发送不同ID/数据，USART2发送原始字节并使用板上ID，核对三路CAN侧均可收到；协议格式和数据不得串扰。
4. 当前RAW版须先恢复外部单帧正反向，再完成USART2长文本/独立反向、USART1 / USB回归和三路同时转发；此前USB测试为旧固件。内部回环仅覆盖控制器和软件路径。
5. 按键测试进入设置、1/32/回绕、确认/取消/30秒超时；`id`和`save`后真正断电再读ID。暂停业务流量时测试保存与重插，进一步做受控断电恢复实验。
6. USB↔USART1模式下运行双端口随机字节全双工测试，同时从USART2收发CAN；USB拔出/插回时检查UART和CAN继续工作。
7. Release下测回显RTT/有效吞吐；用逻辑分析仪同时观察UART与CAN，给出真实单向延迟分布。连续压力测试需额外观察FIFO丢帧、TEC/REC、busoff及任务栈余量。

USBFS在本器件上使用PMA/IRQ/缓冲区，不声称USB DMA；仅UART使用DMA。Flash擦写暂停同Bank取指，配置保存时不承诺业务流低延迟；UART无RTS/CTS，不能承诺过量持续输入零丢包。

## 复现主机验证

工作区根目录：

```bash
xvfb-run -a ./tools/test_host.sh
cmake --preset Debug && cmake --build --preset Debug
cmake --preset Release && cmake --build --preset Release
python3 -m py_compile tools/*.py
```

需要主机GCC、Python3、pyserial；PTY测试使用Linux/POSIX。主机测试二进制保存在 `build/host/`。

## 历史实板记录（前版测试固件）

以下内容发生在当前产品开发版之前，仅保留排障证据。

## 实板烧录与复位排障

2026-10-31 接入 Horco CMSIS-DAP（`FAED:4873`），使用本机 xPack OpenOCD `0.12.0+dev-01685-gb9224c0c0`。

- SWD 可识别 Cortex-M4、可读 Flash，普通暂停成功；100 kHz、500 kHz 下原生 `reset halt` 仍会超时，单纯降低速度未解决问题。
- 读出启动选项 `FLASH_OPTR=0xF3EFF8AA`：`nSWBOOT0=0, nBOOT0=0, nBOOT1=1`，软件选择系统 Bootloader。暂停时 PC 在 `0x1FFF3Bxx`。
- 先备份 Flash，再以掩码 `0x08000000` 单独将 `nBOOT0` 置 1：`OPTR=0xFBEFF8AA`；RDP 仍为 `0xAA`（Level 0），其余选项位保留。重载后，地址 `0x00000000` 映射 Flash，程序从 Flash 运行。
- 2026-11-06 该选项又回到默认值 `0xF3EFF8AA`，已重新修复；原因与本次处理见文首「启动选项回退与恢复」。
- 修复启动选项后，原生 `reset halt` 仍无法捕获复位暂停；设置复位入口硬件断点并执行 `reset run` 可以在第一条启动指令前暂停。将这一实测流程封装为 `shark_reset_halt`，由 `shark_program` 和 Cortex-Debug 使用；常规烧录不修改选项字节。
- `adapter speed 500` 移到 `target/stm32g4x.cfg` 之后，并覆盖 `reset-start` 事件；芯片脚本原本会将初始 / 复位 SWD 速度覆盖为 2 MHz。
- 运行更新后的 VS Code 烧录命令 `openocd -f openocd/daplink_stm32g4.cfg -c 'shark_program {build/Debug/uart_to_can/uart_to_can.elf}'` 成功，写入和校验均通过（49,264 字节），复位运行，退出码 0。
- 使用 `gdb-multiarch` 实际执行 F5 配置对应的复位、`load`、再次复位、临时 `main` 断点与继续；成功停在 `main`，再重复复位并停在 `main` 也成功。已覆盖 `gdb-flash-write-end` 默认的 `reset halt`。
- 运行后读取 `SystemCoreClock=144000000`、`uxCurrentNumberOfTasks=3`、`xFreeBytesRemaining=4392`。继续运行 250 ms，HAL Tick 从 46825 增至 47076，RTOS Tick 从 46824 增至 47074；任务调度与两种时基均在运行。

芯片标识差异：用户再次确认板上标识 **G431CBT6**；实际读到的 `DBGMCU_IDCODE=0x20036469` 被 OpenOCD 识别为 G47/G48，Flash 容量寄存器为 128 KiB，工具按双 Bank、每 Bank 64 KiB 处理（起始地址分别 `0x08000000`、`0x08040000`）。保持工程的 G431CBT6 配置；当前固件小于 64 KiB，已实际成功烧录和运行。芯片 ID / 标识差异的原因尚未确定，未来镜像超过 64 KiB 时需要先确认真实 Flash 布局，不能据此认为全部 128 KiB 连续可用。

备份：`build/backups/before-boot-fix-20261031-bank1.bin`、`...-bank2.bin`，各 64 KiB；地址、SHA-256 和原始 OPTR 见同目录 `before-boot-fix-20261031.json`。`before-boot-fix-20261031.bin` 是最初按连续 128 KiB 读取的副本，第二个 Bank 应以上述独立备份为准。


## 历史LED与用户确认

用户确认前版CAN控制器内部回环通过，并纠正四颗LED为低电平点亮；IOC与本版生成的PWM极性均已改为LOW。此前检测到过 `/dev/ttyACM0` 为SHARK UART CAN（0483:5740），证实前版USB枚举；本次结束时未检测到该设备。

完整固件包位于D盘Repository，官方ZIP大小/MD5与元数据一致，SHA-256见 `firmware_packages.json`。产品重构前源码备份：`build/backups/before-product-20261031.tar.gz`。

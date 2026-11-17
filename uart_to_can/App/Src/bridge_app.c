#include "bridge_app.h"
#include "bridge_protocol.h"
#include "bridge_ring.h"
#include "bridge_store.h"
#include "bridge_ui.h"
#include "board_config.h"
#include "main.h"
#include "fdcan.h"
#include "usart.h"
#include "tim.h"
#include "usb_device.h"
#include "usbd_cdc_if.h"
#include "FreeRTOS.h"
#include "task.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

extern USBD_HandleTypeDef hUsbDeviceFS;
#define DMA_SIZE 64U
#define TX_SIZE 256U
#define RX_FRAMES 64U
#define TX_FRAMES 32U
#define UART2_PORT 1U
#define USB_PORT 2U
#define PORT_COUNT 3U
#define WIRE_ASCII 0U
#define WIRE_BINARY 1U
#define WIRE_RAW 2U

typedef struct {
    BridgeRing rx, tx;
    BridgeDecoder decoder;
    UART_HandleTypeDef *uart;
    uint8_t dma[DMA_SIZE], sending[TX_SIZE];
    uint16_t dma_position;
    uint8_t wire;
    volatile uint8_t restart_rx, overflow;
    volatile uint32_t rx_bytes, rx_drop, errors, arrival_cycle;
    uint32_t tx_bytes, tx_drop, parse_errors, dispatch_us_max;
} Port;
static Port ports[PORT_COUNT];
static BridgeFrame can_in[RX_FRAMES], can_out[TX_FRAMES];
static volatile uint8_t can_in_head, can_in_tail;
static uint8_t can_out_head, can_out_tail;
static volatile uint32_t can_drop, can_fifo_lost, can_sent;
static volatile uint32_t can_active;
static uint32_t can_rx, can_queued, can_rejected;
static TaskHandle_t bridge_task;
static volatile uint8_t usb_reset, usb_rearm;
static uint8_t can_state, led_mask = 0xff, saved_id;
static BridgeUI ui;
static const char *const mode_names[] = {"uart-can", "usb-can", "usb-ttl"};
static const char *const port_names[] = {"UART1", "UART2", "USB"};
static const uint32_t uart2_baud_options[] = {
    9600U, 19200U, 38400U, 57600U, 115200U, 230400U,
    460800U, 921600U, 1000000U, 1500000U, 2000000U, 3000000U
};
static uint32_t uart2_baud;
static uint32_t uart2_baud_pending;
static uint8_t uart2_baud_reply_port;

static void configure_ports(void)
{
    ports[0].uart = &huart1;
    ports[UART2_PORT].uart = &huart2;
    /* This port belongs to an external serial device, not the SHARK console.
     * Set at runtime to keep the large port buffers in zero-initialized BSS. */
    ports[UART2_PORT].wire = WIRE_RAW;
    uart2_baud = BOARD_UART2_BAUD_DEFAULT;
    huart2.Init.BaudRate = BOARD_UART2_BAUD_DEFAULT;
    uart2_baud_pending = 0;
}

static void wake_from_isr(void)
{
    if (!bridge_task) return;
    BaseType_t wake = pdFALSE;
    vTaskNotifyGiveFromISR(bridge_task, &wake);
    portYIELD_FROM_ISR(wake);
}
void Bridge_USBTransmitComplete(void) { wake_from_isr(); }

static int usb_connected(void)
{
    return hUsbDeviceFS.dev_state == USBD_STATE_CONFIGURED && hUsbDeviceFS.pClassData;
}
static int transparent(unsigned index)
{ return ui.mode == BRIDGE_USB_TTL && (index == 0 || index == USB_PORT); }
static int put_bytes(unsigned index, const uint8_t *bytes, unsigned length)
{
    Port *p = &ports[index];
    if (length > Bridge_RingFree(&p->tx)) { p->tx_drop += length; return 0; }
    Bridge_RingWrite(&p->tx, bytes, length);
    return 1;
}
static void output(unsigned index, const char *text)
{ put_bytes(index, (const uint8_t *)text, (unsigned)strlen(text)); }
static void control_notice(const char *text)
{
    /* UART2 is always an external raw device. Notices only go to framed ports;
     * USB/UART1 also stay silent while serving the transparent USB-TTL link. */
    for (unsigned i = 0; i < PORT_COUNT; ++i)
        if (i != UART2_PORT && !transparent(i) && ports[i].wire != WIRE_RAW) output(i, text);
}
static void packet_output(unsigned index, const BridgePacket *packet)
{
    uint8_t bytes[BRIDGE_PACKET_MAX];
    unsigned length = (unsigned)Bridge_Encode(packet, bytes, sizeof(bytes));
    if (length) put_bytes(index, bytes, length);
}
static void receive(Port *p, const uint8_t *data, unsigned length)
{
    if (!Bridge_RingCount(&p->rx)) p->arrival_cycle = DWT->CYCCNT;
    unsigned copied = Bridge_RingWrite(&p->rx, data, length);
    p->rx_bytes += length;
    if (copied != length) { p->rx_drop += length - copied; p->overflow = 1; }
    wake_from_isr();
}
void Bridge_USBReceive(const uint8_t *data, uint32_t length)
{
    receive(&ports[USB_PORT], data, length);
    usb_rearm = 1;
    wake_from_isr();
}
void Bridge_USBReset(void)
{
    ports[USB_PORT].rx.tail = ports[USB_PORT].rx.head;
    usb_rearm = 0;
    usb_reset = 1;
    wake_from_isr();
}
static void arm_usb(void)
{
    taskENTER_CRITICAL();
    if (usb_rearm && usb_connected() && Bridge_RingFree(&ports[USB_PORT].rx) >= 64U) {
        if (USBD_CDC_ReceivePacket(&hUsbDeviceFS) == USBD_OK) usb_rearm = 0;
    }
    taskEXIT_CRITICAL();
}
static int pump_tx(unsigned index)
{
    Port *p = &ports[index];
    unsigned length = Bridge_RingCount(&p->tx);
    if (!length) return 0;
    if (p->uart) {
        if (p->uart->gState != HAL_UART_STATE_READY) return 0;
    } else {
        taskENTER_CRITICAL();
        int connected = usb_connected();
        int busy = connected && ((USBD_CDC_HandleTypeDef *)hUsbDeviceFS.pClassData)->TxState;
        taskEXIT_CRITICAL();
        if (!connected) { p->tx_drop += length; p->tx.tail = p->tx.head; return 0; }
        if (busy) return 0;
    }
    if (length > TX_SIZE) length = TX_SIZE;
    /* Staging remains immutable until DMA/USB completion. Ring storage can be
     * reclaimed immediately without DMA reading bytes that have been overwritten. */
    unsigned tail = p->tx.tail;
    for (unsigned i = 0; i < length; ++i) p->sending[i] = p->tx.bytes[(tail + i) & (BRIDGE_RING_SIZE - 1U)];
    int ok;
    if (p->uart) ok = HAL_UART_Transmit_DMA(p->uart, p->sending, length) == HAL_OK;
    else {
        taskENTER_CRITICAL();
        ok = usb_connected() && CDC_Transmit_FS(p->sending, (uint16_t)length) == USBD_OK;
        taskEXIT_CRITICAL();
    }
    if (ok) {
        p->tx.tail = (uint16_t)((tail + length) & (BRIDGE_RING_SIZE - 1U));
        p->tx_bytes += length;
    }
    return ok;
}
static unsigned tx_frame_free(void)
{ return TX_FRAMES - 1U - ((can_out_head - can_out_tail) & (TX_FRAMES - 1U)); }
static int set_can(unsigned state)
{
    HAL_GPIO_WritePin(CAN_STB_GPIO_Port, CAN_STB_Pin, BOARD_CAN_STB_STANDBY);
    if (HAL_FDCAN_Stop(&hfdcan1) != HAL_OK || HAL_FDCAN_DeInit(&hfdcan1) != HAL_OK) return 0;
    __HAL_RCC_FDCAN_FORCE_RESET(); __DSB(); __HAL_RCC_FDCAN_RELEASE_RESET();
    HAL_NVIC_ClearPendingIRQ(FDCAN1_IT0_IRQn);
    can_in_tail = can_in_head;
    can_out_tail = can_out_head;
    can_active = 0;
    hfdcan1.Init.Mode = state == 1 ? FDCAN_MODE_INTERNAL_LOOPBACK : FDCAN_MODE_NORMAL;
    if (HAL_FDCAN_Init(&hfdcan1) != HAL_OK) return 0;
    FDCAN_FilterTypeDef filter = {0};
    filter.IdType = FDCAN_STANDARD_ID;
    filter.FilterType = FDCAN_FILTER_MASK;
    filter.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
    if (HAL_FDCAN_ConfigFilter(&hfdcan1, &filter) != HAL_OK) return 0;
    filter.IdType = FDCAN_EXTENDED_ID;
    if (HAL_FDCAN_ConfigFilter(&hfdcan1, &filter) != HAL_OK ||
        HAL_FDCAN_ConfigGlobalFilter(&hfdcan1, FDCAN_REJECT, FDCAN_REJECT, FDCAN_REJECT_REMOTE, FDCAN_REJECT_REMOTE) != HAL_OK ||
        HAL_FDCAN_Start(&hfdcan1) != HAL_OK ||
        HAL_FDCAN_ActivateNotification(&hfdcan1, FDCAN_IT_RX_FIFO0_NEW_MESSAGE | FDCAN_IT_RX_FIFO0_MESSAGE_LOST |
            FDCAN_IT_TX_COMPLETE | FDCAN_IT_BUS_OFF, FDCAN_TX_BUFFER0 | FDCAN_TX_BUFFER1 | FDCAN_TX_BUFFER2) != HAL_OK) return 0;
    can_state = (uint8_t)state;
    if (state == 0) HAL_GPIO_WritePin(CAN_STB_GPIO_Port, CAN_STB_Pin, BOARD_CAN_STB_NORMAL);
    return 1;
}
static void append_frame(const BridgeFrame *frame)
{
    can_out[can_out_head] = *frame;
    can_out_head = (uint8_t)((can_out_head + 1U) & (TX_FRAMES - 1U));
    ++can_queued;
}
static int enqueue(const BridgeFrame *frame)
{
    if (can_state == 2 || !tx_frame_free() || (hfdcan1.Instance->PSR & FDCAN_PSR_BO)) return 0;
    append_frame(frame);
    return 1;
}
static int pump_can_tx(void)
{
    int moved = 0;
    while (can_out_tail != can_out_head && HAL_FDCAN_GetTxFifoFreeLevel(&hfdcan1)) {
        BridgeFrame *f = &can_out[can_out_tail];
        FDCAN_TxHeaderTypeDef header = {0};
        header.Identifier = f->id;
        header.IdType = f->extended ? FDCAN_EXTENDED_ID : FDCAN_STANDARD_ID;
        header.TxFrameType = FDCAN_DATA_FRAME;
        header.DataLength = f->length; /* G4 HAL: unshifted DLC. */
        header.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
        header.BitRateSwitch = FDCAN_BRS_OFF;
        header.FDFormat = FDCAN_CLASSIC_CAN;
        header.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
        taskENTER_CRITICAL();
        /* TXBTO bits persist until the buffer is reused. Account once, including
         * completions whose IRQ has not run yet, before reusing a hardware slot. */
        uint32_t done = hfdcan1.Instance->TXBTO & can_active;
        can_sent += (uint32_t)__builtin_popcount(done);
        can_active &= ~done;
        int ok = HAL_FDCAN_AddMessageToTxFifoQ(&hfdcan1, &header, f->data) == HAL_OK;
        if (ok) can_active |= HAL_FDCAN_GetLatestTxFifoQRequestBuffer(&hfdcan1);
        taskEXIT_CRITICAL();
        if (!ok) break;
        can_out_tail = (uint8_t)((can_out_tail + 1U) & (TX_FRAMES - 1U));
        moved = 1;
    }
    return moved;
}
void HAL_FDCAN_RxFifo0Callback(FDCAN_HandleTypeDef *can, uint32_t flags)
{
    if (flags & FDCAN_IT_RX_FIFO0_MESSAGE_LOST) ++can_fifo_lost;
    while (HAL_FDCAN_GetRxFifoFillLevel(can, FDCAN_RX_FIFO0)) {
        FDCAN_RxHeaderTypeDef header;
        uint8_t data[64];
        if (HAL_FDCAN_GetRxMessage(can, FDCAN_RX_FIFO0, &header, data) != HAL_OK) break;
        if (header.FDFormat != FDCAN_CLASSIC_CAN || header.RxFrameType != FDCAN_DATA_FRAME || header.DataLength > 8) continue;
        uint8_t next = (uint8_t)((can_in_head + 1U) & (RX_FRAMES - 1U));
        if (next == can_in_tail) { ++can_drop; continue; }
        BridgeFrame *f = &can_in[can_in_head];
        f->id = header.Identifier; f->extended = header.IdType == FDCAN_EXTENDED_ID;
        f->length = (uint8_t)header.DataLength;
        memcpy(f->data, data, f->length);
        __DMB(); can_in_head = next;
    }
    wake_from_isr();
}
void HAL_FDCAN_TxBufferCompleteCallback(FDCAN_HandleTypeDef *can, uint32_t buffers)
{
    (void)can;
    can_sent += (uint32_t)__builtin_popcount(buffers & can_active);
    can_active &= ~buffers;
    wake_from_isr();
}
void HAL_FDCAN_ErrorStatusCallback(FDCAN_HandleTypeDef *can, uint32_t flags)
{ (void)can; (void)flags; wake_from_isr(); }
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *uart, uint16_t size)
{
    Port *p = uart == &huart1 ? &ports[0] : &ports[1];
    (void)size;
    /* IDLE can observe bytes past an HT/TC interrupt that is still pending.
     * Read the live DMA cursor so a delayed HT callback cannot look like a wrap
     * and copy old bytes twice. Represent the wrap boundary as DMA_SIZE. */
    size = (uint16_t)(DMA_SIZE - __HAL_DMA_GET_COUNTER(uart->hdmarx));
    if (!size) size = DMA_SIZE;
    if (size < p->dma_position) {
        receive(p, p->dma + p->dma_position, DMA_SIZE - p->dma_position);
        p->dma_position = 0;
    }
    receive(p, p->dma + p->dma_position, size - p->dma_position);
    p->dma_position = size;
}
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *uart)
{ (void)uart; wake_from_isr(); }
void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
    Port *p = uart == &huart1 ? &ports[0] : &ports[1];
    ++p->errors; p->restart_rx = 1; wake_from_isr();
}
static void change_mode(unsigned mode)
{
    if (mode == ui.mode) return;
    /* A configuration boundary discards queued old reports. An already active
     * DMA/USB transfer completes before the command acknowledgement/new data. */
    for (unsigned i = 0; i < PORT_COUNT; ++i) {
        Bridge_DecoderReset(&ports[i].decoder);
        if (i != 1) {
            ports[i].tx_drop += Bridge_RingCount(&ports[i].tx);
            ports[i].tx.tail = ports[i].tx.head;
        }
    }
    ui.mode = (uint8_t)mode; led_mask = 0xff;
}
static void change_id(uint8_t id)
{
    ui.id = id; ui.candidate = id;
    if (!set_can(can_state)) Error_Handler();
}
static unsigned port_index(const char *name)
{
    if (!strcmp(name, "uart1")) return 0;
    if (!strcmp(name, "uart2")) return 1;
    if (!strcmp(name, "usb")) return 2;
    return PORT_COUNT;
}
static int uart2_baud_supported(unsigned long baud)
{
    for (unsigned i = 0; i < sizeof(uart2_baud_options) / sizeof(uart2_baud_options[0]); ++i)
        if (baud == uart2_baud_options[i]) return 1;
    return 0;
}
static void uart2_baud_command(unsigned index, const char *line)
{
    const char *argument = line + 5;
    char *end;
    unsigned long baud;
    if (index == UART2_PORT) return; /* USART2 is a protocol-free data port. */
    if (strcmp(argument, "uart2") && strncmp(argument, "uart2 ", 6)) {
        output(index, "ERR baud uart2 [rate]\r\n");
        return;
    }
    argument += 5;
    if (!*argument) {
        char response[48];
        snprintf(response, sizeof(response), "uart2_baud=%lu\r\n", (unsigned long)uart2_baud);
        output(index, response);
        return;
    }
    if (*argument != ' ') {
        output(index, "ERR baud uart2 [rate]\r\n");
        return;
    }
    ++argument;
    baud = strtoul(argument, &end, 10);
    if (end == argument || *end || !uart2_baud_supported(baud)) {
        output(index, "ERR baud uart2 [9600/19200/38400/57600/115200/230400/460800/921600/1000000/1500000/2000000/3000000]\r\n");
        return;
    }
    if (uart2_baud_pending) { output(index, "ERR UART2 baud change pending\r\n"); return; }
    if (baud == uart2_baud) {
        char response[64];
        snprintf(response, sizeof(response), "OK uart2_baud=%lu unchanged\r\n", (unsigned long)uart2_baud);
        output(index, response);
        return;
    }
    uart2_baud_pending = (uint32_t)baud;
    uart2_baud_reply_port = (uint8_t)index;
}
static void apply_uart2_baud(void)
{
    Port *p = &ports[UART2_PORT];
    if (!uart2_baud_pending) return;

    uint32_t baud = uart2_baud_pending;
    /* Quiesce both UART and DMA callbacks while stopping the circular RX DMA.
     * Otherwise an IDLE/half-transfer callback could enqueue bytes between the
     * empty-ring check and the baud-rate reconfiguration. */
    HAL_NVIC_DisableIRQ(USART2_IRQn);
    HAL_NVIC_DisableIRQ(DMA1_Channel2_IRQn);
    HAL_NVIC_DisableIRQ(DMA1_Channel4_IRQn);
    if (Bridge_RingCount(&p->rx) || Bridge_RingCount(&p->tx) ||
        p->uart->gState != HAL_UART_STATE_READY) {
        HAL_NVIC_EnableIRQ(DMA1_Channel2_IRQn);
        HAL_NVIC_EnableIRQ(DMA1_Channel4_IRQn);
        HAL_NVIC_EnableIRQ(USART2_IRQn);
        return;
    }
    if (HAL_UART_AbortTransmit(p->uart) != HAL_OK || HAL_UART_AbortReceive(p->uart) != HAL_OK) Error_Handler();
    unsigned dma_position = DMA_SIZE - __HAL_DMA_GET_COUNTER(p->uart->hdmarx);
    if (dma_position >= DMA_SIZE) dma_position = 0;
    unsigned dma_pending = (dma_position + DMA_SIZE - p->dma_position) % DMA_SIZE;
    p->rx_drop += dma_pending;
    if (HAL_UART_DeInit(p->uart) != HAL_OK) Error_Handler();
    HAL_NVIC_ClearPendingIRQ(USART2_IRQn);
    HAL_NVIC_ClearPendingIRQ(DMA1_Channel2_IRQn);
    HAL_NVIC_ClearPendingIRQ(DMA1_Channel4_IRQn);
    p->dma_position = 0;
    p->restart_rx = 0;
    p->overflow = 0;
    huart2.Init.BaudRate = baud;
    if (HAL_UART_Init(&huart2) != HAL_OK ||
        HAL_UARTEx_SetTxFifoThreshold(&huart2, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK ||
        HAL_UARTEx_SetRxFifoThreshold(&huart2, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK ||
        HAL_UARTEx_DisableFifoMode(&huart2) != HAL_OK ||
        HAL_UARTEx_ReceiveToIdle_DMA(&huart2, p->dma, DMA_SIZE) != HAL_OK) Error_Handler();
    HAL_NVIC_EnableIRQ(DMA1_Channel2_IRQn);
    HAL_NVIC_EnableIRQ(DMA1_Channel4_IRQn);
    HAL_NVIC_EnableIRQ(USART2_IRQn);

    uart2_baud = baud;
    uart2_baud_pending = 0;
    char response[112];
    snprintf(response, sizeof(response),
             "OK uart2_baud=%lu; set the USART2 adapter now; reboot default=%u\r\n",
             (unsigned long)uart2_baud, (unsigned)BOARD_UART2_BAUD_DEFAULT);
    output(uart2_baud_reply_port, response);
}
static void ascii_command(unsigned index, const char *line)
{
    char response[384];
    BridgeFrame frame;
    if (!strcmp(line, "ping")) output(index, "pong\r\n");
    else if (!strncmp(line, "echo ", 5)) { output(index, line + 5); output(index, "\r\n"); }
    else if (!strcmp(line, "help")) output(index,
        "ping | echo TEXT | stats | mode uart-can/usb-can/usb-ttl\r\n"
        "can normal/loopback/standby | send HEX_ID HEX_DATA | sendx HEX_ID HEX_DATA\r\n"
        "write HEX_DATA | test | id [1..32] | save | id cancel | baud uart2 [rate]\r\n"
        "wire [uart1/usb] ascii/binary/raw | UART2 fixed raw | leds 0..F/auto\r\n");
    else if (!strncmp(line, "baud ", 5)) uart2_baud_command(index, line);
    else if (!strcmp(line, "stats")) {
        FDCAN_ProtocolStatusTypeDef status = {0}; FDCAN_ErrorCountersTypeDef counters = {0};
        HAL_FDCAN_GetProtocolStatus(&hfdcan1, &status); HAL_FDCAN_GetErrorCounters(&hfdcan1, &counters);
        snprintf(response, sizeof(response),
            "mode=%s can_state=%u id=%u saved_id=%u setting=%u candidate=%u uart2_baud=%lu uptime=%lu heap=%u stack=%u "
            "can_rx=%lu can_tx_queued=%lu can_tx_sent=%lu can_tx_err=%lu can_pending=%u "
            "can_drop=%lu fifo_lost=%lu busoff=%lu tec=%lu rec=%lu\r\n",
            mode_names[ui.mode], can_state, ui.id, saved_id, ui.setting, ui.candidate, (unsigned long)uart2_baud,
            (unsigned long)HAL_GetTick(),
            (unsigned)xPortGetFreeHeapSize(), (unsigned)uxTaskGetStackHighWaterMark(NULL),
            (unsigned long)can_rx, (unsigned long)can_queued, (unsigned long)can_sent, (unsigned long)can_rejected,
            (unsigned)((can_out_head - can_out_tail) & (TX_FRAMES - 1U)), (unsigned long)can_drop,
            (unsigned long)can_fifo_lost, (unsigned long)status.BusOff, (unsigned long)counters.TxErrorCnt, (unsigned long)counters.RxErrorCnt);
        output(index, response);
        for (unsigned i = 0; i < PORT_COUNT; ++i) {
            Port *p = &ports[i];
            snprintf(response, sizeof(response), "port=%s wire=%u rx=%lu tx=%lu rx_drop=%lu tx_drop=%lu errors=%lu parse_err=%lu dispatch_us_max=%lu\r\n",
                port_names[i], p->wire, (unsigned long)p->rx_bytes, (unsigned long)p->tx_bytes,
                (unsigned long)p->rx_drop, (unsigned long)p->tx_drop, (unsigned long)p->errors,
                (unsigned long)p->parse_errors, (unsigned long)p->dispatch_us_max);
            output(index, response);
        }
    } else if (!strncmp(line, "mode ", 5)) {
        const char *name = line + 5;
        if (!strcmp(name, "loopback") || !strcmp(name, "standby") || !strcmp(name, "bridge")) {
            unsigned state = !strcmp(name, "loopback") ? 1 : !strcmp(name, "standby") ? 2 : 0;
            if (!set_can(state)) Error_Handler();
            snprintf(response, sizeof(response), "OK mode=%s\r\n", name); output(index, response);
        } else if (!strcmp(name, "id")) {
            ui.setting = 1; ui.clicks = 0; ui.candidate = ui.id; ui.last_action = HAL_GetTick(); led_mask = 0xff;
            output(index, "OK ID setting; click 1..32 times; hold 1s to save\r\n");
        } else {
            unsigned mode;
            for (mode = 0; mode < BRIDGE_MODE_COUNT; ++mode) if (!strcmp(name, mode_names[mode])) break;
            if (mode == BRIDGE_MODE_COUNT) output(index, "ERR mode\r\n");
            else { change_mode(mode); snprintf(response, sizeof(response), "OK mode=%s\r\n", name); output(index, response); }
        }
    } else if (!strncmp(line, "can ", 4)) {
        unsigned state = !strcmp(line + 4, "normal") ? 0 : !strcmp(line + 4, "loopback") ? 1 : !strcmp(line + 4, "standby") ? 2 : 3;
        if (state == 3) output(index, "ERR can state\r\n");
        else { if (!set_can(state)) Error_Handler(); output(index, "OK can\r\n"); }
    } else if (!strcmp(line, "test") || !strncmp(line, "send ", 5) || !strncmp(line, "sendx ", 6) || !strncmp(line, "write ", 6)) {
        int valid;
        if (!strcmp(line, "test")) { frame = (BridgeFrame){.id = ui.id, .length = 8, .data = {0x55,0xaa,0,1,2,3,4,5}}; valid = 1; }
        else if (!strncmp(line, "write ", 6)) {
            snprintf(response, sizeof(response), "send %X %s", ui.id, line + 6); valid = Bridge_ParseFrame(response, &frame);
        } else valid = Bridge_ParseFrame(line, &frame);
        if (!valid) output(index, "ERR frame (8 data bytes max, '-' for empty)\r\n");
        else if (enqueue(&frame)) output(index, "OK queued\r\n");
        else { ++can_rejected; output(index, "ERR CAN standby/busy/busoff\r\n"); }
    } else if (!strcmp(line, "id")) {
        snprintf(response, sizeof(response), "id=%u hex=%03X saved_id=%u\r\n", ui.id, ui.id, saved_id); output(index, response);
    } else if (!strcmp(line, "id cancel")) { ui.setting = 0; output(index, "OK ID setting cancelled\r\n"); }
    else if (!strncmp(line, "id ", 3)) {
        char *end; unsigned long id = strtoul(line + 3, &end, 10);
        if (end == line + 3 || *end || id < 1 || id > 32) output(index, "ERR id 1..32 decimal\r\n");
        else { change_id((uint8_t)id); output(index, "OK id; save to persist\r\n"); }
    } else if (!strcmp(line, "save")) {
        if (Bridge_SettingsSave(ui.id)) { saved_id = ui.id; output(index, "OK saved\r\n"); }
        else output(index, "ERR flash save\r\n");
    } else if (!strncmp(line, "wire ", 5)) {
        char name[12] = {0}; unsigned target = index;
        const char *value = line + 5, *space = strchr(value, ' ');
        if (space) {
            size_t length = (size_t)(space - value);
            if (length < sizeof(name)) { memcpy(name, value, length); target = port_index(name); }
            else target = PORT_COUNT;
            value = space + 1;
        }
        unsigned wire = !strcmp(value, "ascii") ? WIRE_ASCII : !strcmp(value, "binary") ? WIRE_BINARY : !strcmp(value, "raw") ? WIRE_RAW : 3;
        if (target >= PORT_COUNT || wire > WIRE_RAW) output(index, "ERR wire [uart1/usb] ascii/binary/raw\r\n");
        else if (target == UART2_PORT && wire != WIRE_RAW) output(index, "ERR UART2 is a dedicated raw CAN bridge\r\n");
        else {
            ports[target].tx_drop += Bridge_RingCount(&ports[target].tx);
            ports[target].tx.tail = ports[target].tx.head;
            ports[target].wire = (uint8_t)wire;
            Bridge_DecoderReset(&ports[target].decoder);
            output(index, "OK wire\r\n");
        }
    } else if (!strcmp(line, "leds auto")) { led_mask = 0xff; output(index, "OK leds auto\r\n"); }
    else if (!strncmp(line, "leds ", 5)) {
        char *end; unsigned long value = strtoul(line + 5, &end, 16);
        if (end == line + 5 || *end || value > 15) output(index, "ERR leds mask 0..F\r\n");
        else { led_mask = (uint8_t)value; output(index, "OK leds\r\n"); }
    } else if (*line) output(index, "ERR unknown command; type help\r\n");
}
static void binary_command(unsigned index, BridgePacket *p)
{
    uint8_t status = 0;
    if (p->type == BRIDGE_CAN_TX || p->type == BRIDGE_STREAM_TX) {
        uint32_t id = p->flags & BRIDGE_FLAG_DEFAULT_ID ? ui.id : p->id;
        unsigned frames = p->length ? (p->length + 7U) / 8U : 1;
        if ((p->flags & ~(BRIDGE_FLAG_EXT | BRIDGE_FLAG_ACK | BRIDGE_FLAG_DEFAULT_ID)) ||
            id > ((p->flags & BRIDGE_FLAG_EXT) ? 0x1fffffffU : 0x7ffU) ||
            (p->type == BRIDGE_CAN_TX && p->length > 8)) status = 1;
        else if (can_state == 2 || tx_frame_free() < frames || (hfdcan1.Instance->PSR & FDCAN_PSR_BO)) { status = 2; ++can_rejected; }
        else {
            for (unsigned i = 0; i < frames; ++i) {
                BridgeFrame frame = {.id = id, .extended = !!(p->flags & BRIDGE_FLAG_EXT)};
                unsigned at = i * 8U, length = p->length - at;
                if (length > 8) length = 8;
                frame.length = (uint8_t)length; memcpy(frame.data, p->data + at, length);
                append_frame(&frame);
            }
        }
    } else if (p->type == BRIDGE_ECHO) {
        p->type = BRIDGE_ECHO_REPLY; packet_output(index, p); return;
    } else if (p->type == BRIDGE_INFO && !p->length) {
        p->type = BRIDGE_INFO_REPLY; p->length = 9;
        p->data[0] = ui.mode; p->data[1] = ui.id; p->data[2] = saved_id; p->data[3] = can_state;
        p->data[4] = ui.setting; p->data[5] = ports[0].wire; p->data[6] = ports[1].wire; p->data[7] = ports[2].wire;
        p->data[8] = (uint8_t)index;
        packet_output(index, p); return;
    } else if (p->type == BRIDGE_MODE && p->length == 1 && p->data[0] < BRIDGE_MODE_COUNT) change_mode(p->data[0]);
    else if (p->type == BRIDGE_ID && p->length == 1 && p->data[0] >= 1 && p->data[0] <= 32) change_id(p->data[0]);
    else if (p->type == BRIDGE_SAVE && !p->length) {
        if (Bridge_SettingsSave(ui.id)) saved_id = ui.id; else status = 3;
    } else if (p->type == BRIDGE_CAN_TEST && p->length == 1 && p->data[0] < 3) {
        if (!set_can(p->data[0])) Error_Handler();
    } else status = 1;
    if (status || (p->flags & BRIDGE_FLAG_ACK) || (p->type >= BRIDGE_MODE && p->type <= BRIDGE_CAN_TEST)) {
        uint8_t request = p->type;
        p->type = BRIDGE_ACK; p->flags = 0; p->length = 2; p->data[0] = request; p->data[1] = status;
        packet_output(index, p);
    }
}
static unsigned read_bytes(unsigned index, uint8_t *bytes, unsigned length)
{
    if (index != USB_PORT) return Bridge_RingRead(&ports[index].rx, bytes, length);
    taskENTER_CRITICAL();
    unsigned count = usb_reset ? 0 : Bridge_RingRead(&ports[index].rx, bytes, length);
    taskEXIT_CRITICAL();
    return count;
}
static int pump_rx(unsigned index)
{
    Port *p = &ports[index];
    unsigned count = Bridge_RingCount(&p->rx);
    if (!count) return 0;
    uint32_t us = (DWT->CYCCNT - p->arrival_cycle) / (SystemCoreClock / 1000000U);
    if (us > p->dispatch_us_max) p->dispatch_us_max = us;
    if (p->overflow) {
        taskENTER_CRITICAL(); p->overflow = 0;
        if (!transparent(index) && p->wire != WIRE_RAW) {
            p->rx.tail = p->rx.head; Bridge_DecoderReset(&p->decoder); p->decoder.state = 3;
            ++p->parse_errors;
        }
        taskEXIT_CRITICAL();
    }
    if (transparent(index)) {
        unsigned target = index == 0 ? USB_PORT : 0;
        if (target == USB_PORT) {
            taskENTER_CRITICAL(); int connected = usb_connected(); taskEXIT_CRITICAL();
            if (!connected) { p->rx.tail = p->rx.head; ports[USB_PORT].tx_drop += count; return 0; }
        }
        unsigned free = Bridge_RingFree(&ports[target].tx);
        if (count > free) count = free;
        if (count > TX_SIZE) count = TX_SIZE;
        uint8_t bytes[TX_SIZE];
        count = read_bytes(index, bytes, count);
        Bridge_RingWrite(&ports[target].tx, bytes, count);
        return count == TX_SIZE && Bridge_RingCount(&p->rx) && Bridge_RingFree(&ports[target].tx);
    }
    if (p->wire == WIRE_RAW) {
        unsigned processed = 0;
        while (Bridge_RingCount(&p->rx) && tx_frame_free() && can_state != 2 && !(hfdcan1.Instance->PSR & FDCAN_PSR_BO) && processed < TX_SIZE) {
            BridgeFrame frame = {.id = ui.id};
            frame.length = (uint8_t)read_bytes(index, frame.data, 8);
            if (!frame.length) break;
            append_frame(&frame);
            processed += frame.length;
        }
        return processed >= TX_SIZE && Bridge_RingCount(&p->rx) && tx_frame_free();
    }
    BridgePacket packet;
    for (unsigned n = 0; n < TX_SIZE; ++n) {
        uint8_t byte;
        unsigned got = read_bytes(index, &byte, 1);
        if (!got) break;
        int result = Bridge_Feed(&p->decoder, byte, &packet);
        if (result == BRIDGE_DECODE_LINE) ascii_command(index, p->decoder.line);
        else if (result == BRIDGE_DECODE_PACKET) binary_command(index, &packet);
        else if (result == BRIDGE_DECODE_ERROR) ++p->parse_errors;
        /* Routing/format can change at this command boundary. */
        if (transparent(index) || p->wire == WIRE_RAW) return Bridge_RingCount(&p->rx) != 0;
    }
    return Bridge_RingCount(&p->rx) != 0;
}
static int pump_can_rx(void)
{
    for (unsigned n = 0; n < 16 && can_in_tail != can_in_head; ++n) {
        BridgeFrame frame = can_in[can_in_tail];
        __DMB(); can_in_tail = (uint8_t)((can_in_tail + 1U) & (RX_FRAMES - 1U)); ++can_rx;
        char text[48]; int length = Bridge_FormatFrame(&frame, text, sizeof(text));
        for (unsigned i = 0; i < PORT_COUNT; ++i) {
            if (transparent(i)) continue;
            if (ports[i].wire == WIRE_RAW) {
                if (!frame.extended && frame.id == ui.id) {
                    if (i == UART2_PORT && uart2_baud_pending) ports[i].tx_drop += frame.length;
                    else put_bytes(i, frame.data, frame.length);
                }
            } else if (ports[i].wire == WIRE_BINARY) {
                BridgePacket packet = {.id = frame.id, .type = BRIDGE_CAN_RX, .flags = frame.extended ? BRIDGE_FLAG_EXT : 0, .length = frame.length};
                memcpy(packet.data, frame.data, frame.length); packet_output(i, &packet);
            } else if (length) put_bytes(i, (const uint8_t *)text, (unsigned)length);
        }
        for (unsigned i = 0; i < PORT_COUNT; ++i) pump_tx(i);
    }
    return can_in_tail != can_in_head;
}
static void ui_tick(uint32_t now)
{
    uint8_t old_mode = ui.mode;
    unsigned event = Bridge_UIUpdate(&ui, now, HAL_GPIO_ReadPin(MODE_SELECT_KEY_GPIO_Port, MODE_SELECT_KEY_Pin) == BOARD_KEY_PRESSED);
    char text[96];
    if (event == BRIDGE_UI_MODE) {
        unsigned mode = ui.mode; ui.mode = old_mode; change_mode(mode);
        snprintf(text, sizeof(text), "KEY mode=%s\r\n", mode_names[mode]); control_notice(text);
    } else if (event == BRIDGE_UI_ENTER) { led_mask = 0xff; control_notice("KEY ID setting; click 1..32 times; hold 1s to save\r\n"); }
    else if (event == BRIDGE_UI_COUNT) {
        snprintf(text, sizeof(text), "KEY candidate=%u\r\n", ui.candidate); control_notice(text);
    } else if (event == BRIDGE_UI_SAVE) {
        if (Bridge_SettingsSave(ui.candidate)) {
            saved_id = ui.candidate; change_id(ui.candidate); ui.setting = 0;
            snprintf(text, sizeof(text), "KEY saved id=%u\r\n", ui.id); control_notice(text);
        } else control_notice("ERR flash save; ID setting still active\r\n");
    } else if (event == BRIDGE_UI_CANCEL) control_notice("KEY ID setting cancelled\r\n");
    unsigned signed_level, bits = 1U << ui.mode;
    if (ui.setting) {
        signed_level = now % 5000U < 2500U ? 999 : 0; /* 0.2 Hz */
        unsigned clicks = ui.clicks ? ui.clicks : ui.id;
        unsigned phase = (now - ui.last_action) % (clicks * 200U + 1000U);
        bits = phase < clicks * 200U && phase % 200U < 100U ? 7U : 0U;
    } else if (ui.mode == BRIDGE_USB_TTL) {
        unsigned phase = now % 2000U;
        signed_level = phase < 1000U ? phase : 2000U - phase;
    } else signed_level = now % (ui.mode == BRIDGE_USB_CAN ? 2000U : 1000U) < (ui.mode == BRIDGE_USB_CAN ? 1000U : 500U) ? 999 : 0;
    if (led_mask != 0xff) { bits = led_mask; signed_level = led_mask & 8U ? 999U : 0U; }
    __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, bits & 1U ? 999U : 0U);
    __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_3, bits & 2U ? 999U : 0U);
    __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_3, bits & 4U ? 999U : 0U);
    __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_2, signed_level);
}
void Bridge_Run(void)
{
    configure_ports();
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0; DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    Bridge_UIInit(&ui, Bridge_SettingsLoad()); saved_id = ui.id;
    bridge_task = xTaskGetCurrentTaskHandle();
    if (HAL_FDCAN_Start(&hfdcan1) != HAL_OK || !set_can(0)) Error_Handler();
    if (HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_1) != HAL_OK || HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_2) != HAL_OK ||
        HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_3) != HAL_OK || HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_3) != HAL_OK) Error_Handler();
    for (unsigned i = 0; i < 2; ++i)
        if (HAL_UARTEx_ReceiveToIdle_DMA(ports[i].uart, ports[i].dma, DMA_SIZE) != HAL_OK) Error_Handler();
    control_notice("SHARK ready; UART1/UART2 921600; CAN 1000000; ASCII + binary; help\r\n");
    uint32_t last_ui = HAL_GetTick();
    for (;;) {
        if (usb_reset) {
            taskENTER_CRITICAL(); usb_reset = 0;
            Bridge_DecoderReset(&ports[USB_PORT].decoder);
            ports[USB_PORT].tx.tail = ports[USB_PORT].tx.head; taskEXIT_CRITICAL();
        }
        int more = 0;
        unsigned first = ui.mode == BRIDGE_USB_CAN ? USB_PORT : 0;
        for (unsigned step = 0; step < PORT_COUNT; ++step) {
            unsigned index = (first + step) % PORT_COUNT;
            Port *p = &ports[index];
            if (p->restart_rx) {
                HAL_UART_AbortReceive(p->uart); p->dma_position = 0;
                Bridge_DecoderReset(&p->decoder); p->decoder.state = 3; p->restart_rx = 0;
                if (HAL_UARTEx_ReceiveToIdle_DMA(p->uart, p->dma, DMA_SIZE) != HAL_OK) p->restart_rx = 1;
            }
            more |= pump_rx(index);
        }
        pump_can_tx(); more |= pump_can_rx();
        for (unsigned i = 0; i < PORT_COUNT; ++i) pump_tx(i);
        apply_uart2_baud();
        arm_usb();
        uint32_t now = HAL_GetTick();
        if ((uint32_t)(now - last_ui) >= 5U) { ui_tick(now); last_ui = now; }
        /* IRQ notifications remove the old fixed 1 ms delay. Timeout services
         * button debounce/LEDs only; no fill timer is added to the data path. */
        if (!more) ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5));
    }
}

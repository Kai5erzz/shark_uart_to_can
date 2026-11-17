/* Exercise the real application router with peripheral doubles. */
#include <assert.h>
#include <stdlib.h>
#include "../uart_to_can/App/Src/bridge_app.c"

static TestCANRegisters registers;
FDCAN_HandleTypeDef hfdcan1 = {.Instance = &registers};
UART_HandleTypeDef huart1, huart2;
unsigned test_uart_init_count, test_uart_deinit_count, test_uart_rx_start_count;
unsigned test_uart_abort_rx_count, test_uart_abort_tx_count;
static DMA_HandleTypeDef uart2_dma;
TIM_HandleTypeDef htim2, htim3;
USBD_HandleTypeDef hUsbDeviceFS;
uint32_t SystemCoreClock = 144000000U;
TestDWT test_dwt;
TestCoreDebug test_core_debug;
static uint8_t uart_output[2][4096];
static unsigned uart_length[2], saves;

int HAL_UART_Transmit_DMA(UART_HandleTypeDef *uart, uint8_t *data, unsigned length)
{
    unsigned index = uart == &huart2;
    assert(uart_length[index] + length <= sizeof(uart_output[index]));
    memcpy(uart_output[index] + uart_length[index], data, length);
    uart_length[index] += length;
    return HAL_OK;
}
int CDC_Transmit_FS(uint8_t *data, uint16_t length) { return USBD_OK; }
void Error_Handler(void) { abort(); }
uint8_t Bridge_SettingsLoad(void) { return 1; }
int Bridge_SettingsSave(uint8_t id) { ++saves; return 1; }

static void reset_app(unsigned mode, uint8_t id)
{
    memset(ports, 0, sizeof(ports));
    memset(&registers, 0, sizeof(registers));
    memset(uart_length, 0, sizeof(uart_length));
    memset(&ui, 0, sizeof(ui));
    memset(&uart2_dma, 0, sizeof(uart2_dma));
    memset(&huart2, 0, sizeof(huart2));
    memset(&huart1, 0, sizeof(huart1));
    huart2.hdmarx = &uart2_dma;
    uart2_dma.counter = DMA_SIZE;
    test_uart_init_count = test_uart_deinit_count = test_uart_rx_start_count = 0;
    test_uart_abort_rx_count = test_uart_abort_tx_count = 0;
    can_in_head = can_in_tail = can_out_head = can_out_tail = 0;
    can_rx = can_queued = can_rejected = can_drop = can_fifo_lost = can_sent = can_active = saves = 0;
    usb_reset = usb_rearm = can_state = 0;
    configure_ports();
    Bridge_UIInit(&ui, id);
    ui.mode = (uint8_t)mode;
    assert(ports[UART2_PORT].wire == WIRE_RAW);
}
static void drain_can(uint8_t *out, unsigned *length, uint8_t id)
{
    while (can_out_tail != can_out_head) {
        BridgeFrame *frame = &can_out[can_out_tail];
        assert(frame->id == id && !frame->extended && frame->length >= 1 && frame->length <= 8);
        memcpy(out + *length, frame->data, frame->length);
        *length += frame->length;
        can_out_tail = (uint8_t)((can_out_tail + 1U) & (TX_FRAMES - 1U));
    }
}
static void forward_bytes(const uint8_t *data, unsigned length, unsigned chunk, uint8_t id)
{
    uint8_t out[2048];
    unsigned received = 0;
    for (unsigned at = 0; at < length;) {
        unsigned n = length - at;
        if (n > chunk) n = chunk;
        receive(&ports[UART2_PORT], data + at, n);
        at += n;
        while (Bridge_RingCount(&ports[UART2_PORT].rx)) {
            pump_rx(UART2_PORT);
            drain_can(out, &received, id);
        }
    }
    assert(received == length && !memcmp(data, out, length));
    assert(!ports[UART2_PORT].parse_errors && !ports[UART2_PORT].rx_drop);
    assert(!Bridge_RingCount(&ports[UART2_PORT].tx) && !uart_length[1] && !saves);
    assert(ui.id == id);
}
static void raw_input_all_modes(void)
{
    uint8_t payload[1024];
    for (unsigned i = 0; i < 256; ++i) payload[i] = (uint8_t)i;
    const char commands[] = "ping\r\nid 32\r\nsave\nmode usb-ttl\r\nwire uart2 ascii\n";
    unsigned length = 256;
    memcpy(payload + length, commands, sizeof(commands) - 1);
    length += sizeof(commands) - 1;
    for (unsigned type = BRIDGE_MODE; type <= BRIDGE_CAN_TEST; ++type) {
        BridgePacket packet = {.type = type, .length = type == BRIDGE_SAVE ? 0 : 1, .data = {2}};
        length += (unsigned)Bridge_Encode(&packet, payload + length, sizeof(payload) - length);
    }
    const unsigned chunks[] = {1, 3, 8, 17, 32, 64, 257};
    for (unsigned mode = 0; mode < BRIDGE_MODE_COUNT; ++mode)
        for (unsigned i = 0; i < sizeof(chunks) / sizeof(chunks[0]); ++i) {
            uint8_t id = i & 1 ? 32 : 1;
            reset_app(mode, id);
            control_notice("SHARK ready\r\n");
            forward_bytes(payload, length, chunks[i], id);
            assert(ui.mode == mode && can_state == 0);
            assert(ports[UART2_PORT].decoder.state == 0);
        }
}
static void push_received(const BridgeFrame *frame)
{
    can_in[can_in_head] = *frame;
    can_in_head = (uint8_t)((can_in_head + 1U) & (RX_FRAMES - 1U));
    pump_can_rx();
}
static void raw_output_and_id_filter(void)
{
    for (unsigned mode = 0; mode < BRIDGE_MODE_COUNT; ++mode) {
        reset_app(mode, 1);
        uint8_t expected[256];
        unsigned length = 0;
        for (unsigned n = 0; n <= 8; ++n) {
            BridgeFrame frame = {.id = 1, .length = (uint8_t)n, .data = {0, 13, 10, 0xa5, 0xff, 0x55, 1, 2}};
            push_received(&frame);
            memcpy(expected + length, frame.data, n);
            length += n;
            frame.id = 2; push_received(&frame);
            frame.id = 1; frame.extended = 1; push_received(&frame);
        }
        control_notice("KEY saved id=1\r\n");
        pump_tx(UART2_PORT);
        assert(uart_length[1] == length && !memcmp(uart_output[1], expected, length));
        change_id(32);
        BridgeFrame frame = {.id = 1, .length = 2, .data = {0, 255}};
        push_received(&frame);
        assert(uart_length[1] == length);
        frame.id = 32; push_received(&frame);
        assert(uart_length[1] == length + 2 && !memcmp(uart_output[1] + length, frame.data, 2));
        assert(ports[UART2_PORT].wire == WIRE_RAW);
    }
}
static void queue_pressure_and_short_packet(void)
{
    reset_app(BRIDGE_UART_CAN, 1);
    uint8_t bytes[] = {0, '\r', '\n'};
    receive(&ports[UART2_PORT], bytes, sizeof(bytes));
    pump_rx(UART2_PORT);
    assert(can_queued == 1 && can_out[0].length == 3); /* No fill timer / padding. */
    can_out_tail = can_out_head;
    for (unsigned i = 0; i < TX_FRAMES - 1U; ++i) {
        BridgeFrame frame = {.id = 1}; append_frame(&frame);
    }
    receive(&ports[UART2_PORT], bytes, sizeof(bytes));
    pump_rx(UART2_PORT);
    assert(Bridge_RingCount(&ports[UART2_PORT].rx) == sizeof(bytes));
    can_out_tail = can_out_head;
    registers.PSR = FDCAN_PSR_BO;
    pump_rx(UART2_PORT);
    assert(Bridge_RingCount(&ports[UART2_PORT].rx) == sizeof(bytes));
    registers.PSR = 0; can_state = 2;
    pump_rx(UART2_PORT);
    assert(Bridge_RingCount(&ports[UART2_PORT].rx) == sizeof(bytes));
    can_state = 0;
    pump_rx(UART2_PORT);
    assert(!Bridge_RingCount(&ports[UART2_PORT].rx));
    unsigned length = 0; uint8_t out[8];
    drain_can(out, &length, 1);
    assert(length == sizeof(bytes) && !memcmp(out, bytes, length));

    uint8_t large[1400], collected[1400];
    for (unsigned i = 0; i < sizeof(large); ++i) large[i] = (uint8_t)i;
    receive(&ports[UART2_PORT], large, sizeof(large));
    assert(ports[UART2_PORT].rx_drop == sizeof(large) - BRIDGE_RING_SIZE + 1U);
    length = 0;
    while (Bridge_RingCount(&ports[UART2_PORT].rx)) {
        pump_rx(UART2_PORT); drain_can(collected, &length, 1);
    }
    assert(length == BRIDGE_RING_SIZE - 1U && !memcmp(collected, large, length));
    assert(!ports[UART2_PORT].parse_errors);
}
static void protocol_ports_and_configuration_guard(void)
{
    reset_app(BRIDGE_UART_CAN, 1);
    for (unsigned index = 0; index < PORT_COUNT; index += 2) {
        receive(&ports[index], (const uint8_t *)"ping\r\n", 6);
        pump_rx(index);
        uint8_t out[256];
        unsigned n = Bridge_RingRead(&ports[index].tx, out, sizeof(out));
        assert(n == 6 && !memcmp(out, "pong\r\n", 6));
        ascii_command(index, "wire uart2 ascii");
        assert(ports[UART2_PORT].wire == WIRE_RAW);
        n = Bridge_RingRead(&ports[index].tx, out, sizeof(out));
        assert(n > 4 && !memcmp(out, "ERR ", 4));
        ascii_command(index, "wire uart2 binary");
        assert(ports[UART2_PORT].wire == WIRE_RAW);
        ascii_command(index, "uart2 unwanted log");
        assert(!Bridge_RingCount(&ports[UART2_PORT].tx));
        ports[index].tx.tail = ports[index].tx.head;
    }
    ascii_command(USB_PORT, "save");
    assert(saves == 1);
    for (unsigned mode = 0; mode < BRIDGE_MODE_COUNT; ++mode) {
        change_mode(mode);
        assert(ports[UART2_PORT].wire == WIRE_RAW);
    }
    assert(!uart_length[1]);
}
static void uart2_runtime_baud_command(void)
{
    reset_app(BRIDGE_UART_CAN, 1);
    char response[128];
    ascii_command(0, "baud uart2");
    unsigned length = Bridge_RingRead(&ports[0].tx, (uint8_t *)response, sizeof(response) - 1);
    response[length] = '\0';
    assert(strstr(response, "uart2_baud=921600") != NULL);
    assert(!uart2_baud_pending && uart2_baud == BOARD_UART2_BAUD_DEFAULT);
    ascii_command(USB_PORT, "baud uart2");
    length = Bridge_RingRead(&ports[USB_PORT].tx, (uint8_t *)response, sizeof(response) - 1);
    response[length] = '\0';
    assert(strstr(response, "uart2_baud=921600") != NULL);

    ascii_command(USB_PORT, "baud uart2 460800");
    assert(uart2_baud_pending == 460800 && uart2_baud == 921600);
    ascii_command(0, "baud uart2 115200");
    length = Bridge_RingRead(&ports[0].tx, (uint8_t *)response, sizeof(response) - 1);
    response[length] = '\0';
    assert(strstr(response, "change pending") != NULL);

    const uint8_t stale_rx[] = {1, 2, 3, 4};
    const uint8_t stale_tx[] = {5, 6, 7};
    Bridge_RingWrite(&ports[UART2_PORT].rx, stale_rx, sizeof(stale_rx));
    Bridge_RingWrite(&ports[UART2_PORT].tx, stale_tx, sizeof(stale_tx));
    ports[UART2_PORT].dma_position = 3;
    uart2_dma.counter = DMA_SIZE - 8;
    apply_uart2_baud();
    assert(uart2_baud == 921600 && uart2_baud_pending == 460800); /* Drain queued data first. */
    ports[UART2_PORT].rx.tail = ports[UART2_PORT].rx.head;
    ports[UART2_PORT].tx.tail = ports[UART2_PORT].tx.head;
    apply_uart2_baud();
    assert(uart2_baud == 460800 && !uart2_baud_pending && huart2.Init.BaudRate == 460800);
    assert(!Bridge_RingCount(&ports[UART2_PORT].rx) && !Bridge_RingCount(&ports[UART2_PORT].tx));
    assert(ports[UART2_PORT].rx_drop == 5);
    assert(ports[UART2_PORT].tx_drop == 0);
    assert(test_uart_abort_tx_count == 1 && test_uart_abort_rx_count == 1);
    assert(test_uart_deinit_count == 1 && test_uart_init_count == 1 && test_uart_rx_start_count == 1);
    length = Bridge_RingRead(&ports[USB_PORT].tx, (uint8_t *)response, sizeof(response) - 1);
    response[length] = '\0';
    assert(strstr(response, "OK uart2_baud=460800") != NULL);

    ascii_command(0, "baud uart2 500000");
    length = Bridge_RingRead(&ports[0].tx, (uint8_t *)response, sizeof(response) - 1);
    response[length] = '\0';
    assert(strstr(response, "ERR baud") != NULL && uart2_baud == 460800);
    ascii_command(0, "baud uart2 460800");
    length = Bridge_RingRead(&ports[0].tx, (uint8_t *)response, sizeof(response) - 1);
    response[length] = '\0';
    assert(strstr(response, "unchanged") != NULL);

    const uint32_t supported[] = {
        9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600,
        1000000, 1500000, 2000000, 3000000
    };
    for (unsigned i = 0; i < sizeof(supported) / sizeof(supported[0]); ++i) {
        char command[40];
        snprintf(command, sizeof(command), "baud uart2 %lu", (unsigned long)supported[i]);
        uint32_t previous = uart2_baud;
        ascii_command(0, command);
        if (supported[i] == previous) {
            assert(!uart2_baud_pending);
        } else {
            assert(uart2_baud_pending == supported[i]);
            apply_uart2_baud();
            assert(uart2_baud == supported[i] && !uart2_baud_pending);
        }
        (void)Bridge_RingRead(&ports[0].tx, (uint8_t *)response, sizeof(response));
    }

    ascii_command(UART2_PORT, "baud uart2 115200");
    assert(uart2_baud == 3000000); /* UART2 data bytes are never interpreted as commands. */
}
int main(void)
{
    raw_input_all_modes(); raw_output_and_id_filter();
    queue_pressure_and_short_packet(); protocol_ports_and_configuration_guard(); uart2_runtime_baud_command();
    puts("PASS app: UART2 raw duplex, runtime baud command, all modes/bytes, ID filter, no command injection, queue pressure");
}

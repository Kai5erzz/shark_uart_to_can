#ifndef BRIDGE_TEST_HAL_H
#define BRIDGE_TEST_HAL_H
/* Minimal peripheral doubles for exercising the real bridge_app.c on a host.
 * CAN/UART timing and DMA behavior still require hardware validation. */
#include <stddef.h>
#include <stdint.h>

typedef struct { uint32_t PSR, TXBTO; } TestCANRegisters;
typedef struct { TestCANRegisters *Instance; struct { unsigned Mode; } Init; } FDCAN_HandleTypeDef;
typedef struct { unsigned counter; } DMA_HandleTypeDef;
typedef struct { unsigned gState; DMA_HandleTypeDef *hdmarx; struct { unsigned BaudRate; } Init; } UART_HandleTypeDef;
typedef struct { unsigned unused; } TIM_HandleTypeDef;
typedef struct { unsigned dev_state; void *pClassData; } USBD_HandleTypeDef;
typedef struct { unsigned TxState; } USBD_CDC_HandleTypeDef;
typedef struct { uint32_t Identifier, IdType, TxFrameType, DataLength, ErrorStateIndicator,
    BitRateSwitch, FDFormat, TxEventFifoControl; } FDCAN_TxHeaderTypeDef;
typedef struct { uint32_t Identifier, IdType, RxFrameType, DataLength, FDFormat; } FDCAN_RxHeaderTypeDef;
typedef struct { unsigned IdType, FilterType, FilterConfig; } FDCAN_FilterTypeDef;
typedef struct { unsigned BusOff; } FDCAN_ProtocolStatusTypeDef;
typedef struct { unsigned TxErrorCnt, RxErrorCnt; } FDCAN_ErrorCountersTypeDef;
typedef struct { uint32_t CYCCNT, CTRL; } TestDWT;
typedef struct { uint32_t DEMCR; } TestCoreDebug;
typedef void *TaskHandle_t;
typedef int BaseType_t;

extern FDCAN_HandleTypeDef hfdcan1;
extern UART_HandleTypeDef huart1, huart2;
extern unsigned test_uart_init_count, test_uart_deinit_count, test_uart_rx_start_count;
extern unsigned test_uart_abort_rx_count, test_uart_abort_tx_count;
extern TIM_HandleTypeDef htim2, htim3;
extern USBD_HandleTypeDef hUsbDeviceFS;
extern uint32_t SystemCoreClock;
extern TestDWT test_dwt;
extern TestCoreDebug test_core_debug;
#define DWT (&test_dwt)
#define CoreDebug (&test_core_debug)
#define CoreDebug_DEMCR_TRCENA_Msk 1U
#define DWT_CTRL_CYCCNTENA_Msk 1U
#define GPIO_PIN_RESET 0U
#define GPIO_PIN_SET 1U
#define CAN_STB_GPIO_Port 0U
#define CAN_STB_Pin 0U
#define MODE_SELECT_KEY_GPIO_Port 0U
#define MODE_SELECT_KEY_Pin 0U
#define FDCAN1_IT0_IRQn 0U
#define USART2_IRQn 1U
#define DMA1_Channel2_IRQn 2U
#define DMA1_Channel4_IRQn 3U
#define HAL_OK 0
#define HAL_UART_STATE_READY 0U
#define UART_TXFIFO_THRESHOLD_1_8 0U
#define UART_RXFIFO_THRESHOLD_1_8 0U
#define USBD_STATE_CONFIGURED 1U
#define USBD_OK 0
#define FDCAN_MODE_INTERNAL_LOOPBACK 1U
#define FDCAN_MODE_NORMAL 0U
#define FDCAN_STANDARD_ID 0U
#define FDCAN_EXTENDED_ID 1U
#define FDCAN_FILTER_MASK 0U
#define FDCAN_FILTER_TO_RXFIFO0 0U
#define FDCAN_REJECT 0U
#define FDCAN_REJECT_REMOTE 0U
#define FDCAN_IT_RX_FIFO0_NEW_MESSAGE 1U
#define FDCAN_IT_RX_FIFO0_MESSAGE_LOST 2U
#define FDCAN_IT_TX_COMPLETE 4U
#define FDCAN_IT_BUS_OFF 8U
#define FDCAN_TX_BUFFER0 1U
#define FDCAN_TX_BUFFER1 2U
#define FDCAN_TX_BUFFER2 4U
#define FDCAN_RX_FIFO0 0U
#define FDCAN_PSR_BO 1U
#define FDCAN_DATA_FRAME 0U
#define FDCAN_ESI_ACTIVE 0U
#define FDCAN_BRS_OFF 0U
#define FDCAN_CLASSIC_CAN 0U
#define FDCAN_NO_TX_EVENTS 0U
#define TIM_CHANNEL_1 1U
#define TIM_CHANNEL_2 2U
#define TIM_CHANNEL_3 3U
#define pdFALSE 0
#define pdTRUE 1
#define pdMS_TO_TICKS(ms) (ms)
#define taskENTER_CRITICAL() ((void)0)
#define taskEXIT_CRITICAL() ((void)0)
#define portYIELD_FROM_ISR(wake) ((void)(wake))
#define __DMB() __atomic_thread_fence(__ATOMIC_SEQ_CST)
#define __DSB() __DMB()
#define __HAL_DMA_GET_COUNTER(dma) ((dma)->counter)
#define __HAL_RCC_FDCAN_FORCE_RESET() ((void)0)
#define __HAL_RCC_FDCAN_RELEASE_RESET() ((void)0)
#define __HAL_TIM_SET_COMPARE(timer, channel, level) ((void)(level))

static inline void vTaskNotifyGiveFromISR(TaskHandle_t task, BaseType_t *wake) { *wake = 0; }
static inline TaskHandle_t xTaskGetCurrentTaskHandle(void) { return NULL; }
static inline unsigned ulTaskNotifyTake(int clear, unsigned timeout) { return 0; }
static inline unsigned xPortGetFreeHeapSize(void) { return 4096; }
static inline unsigned uxTaskGetStackHighWaterMark(void *task) { return 512; }
static inline uint32_t HAL_GetTick(void) { return 100; }
static inline void HAL_GPIO_WritePin(unsigned port, unsigned pin, unsigned level) {}
static inline unsigned HAL_GPIO_ReadPin(unsigned port, unsigned pin) { return GPIO_PIN_SET; }
static inline void HAL_NVIC_ClearPendingIRQ(unsigned irq) {}
static inline void HAL_NVIC_DisableIRQ(unsigned irq) {}
static inline void HAL_NVIC_EnableIRQ(unsigned irq) {}
static inline int HAL_FDCAN_Stop(FDCAN_HandleTypeDef *can) { return HAL_OK; }
static inline int HAL_FDCAN_DeInit(FDCAN_HandleTypeDef *can) { return HAL_OK; }
static inline int HAL_FDCAN_Init(FDCAN_HandleTypeDef *can) { return HAL_OK; }
static inline int HAL_FDCAN_Start(FDCAN_HandleTypeDef *can) { return HAL_OK; }
static inline int HAL_FDCAN_ConfigFilter(FDCAN_HandleTypeDef *can, FDCAN_FilterTypeDef *filter) { return HAL_OK; }
static inline int HAL_FDCAN_ConfigGlobalFilter(FDCAN_HandleTypeDef *can, unsigned a, unsigned b, unsigned c, unsigned d) { return HAL_OK; }
static inline int HAL_FDCAN_ActivateNotification(FDCAN_HandleTypeDef *can, unsigned flags, unsigned buffers) { return HAL_OK; }
static inline unsigned HAL_FDCAN_GetTxFifoFreeLevel(FDCAN_HandleTypeDef *can) { return 0; }
static inline int HAL_FDCAN_AddMessageToTxFifoQ(FDCAN_HandleTypeDef *can, FDCAN_TxHeaderTypeDef *header, const uint8_t *data) { return HAL_OK; }
static inline unsigned HAL_FDCAN_GetLatestTxFifoQRequestBuffer(FDCAN_HandleTypeDef *can) { return 1; }
static inline unsigned HAL_FDCAN_GetRxFifoFillLevel(FDCAN_HandleTypeDef *can, unsigned fifo) { return 0; }
static inline int HAL_FDCAN_GetRxMessage(FDCAN_HandleTypeDef *can, unsigned fifo, FDCAN_RxHeaderTypeDef *header, uint8_t *data) { return 1; }
static inline int HAL_FDCAN_GetProtocolStatus(FDCAN_HandleTypeDef *can, FDCAN_ProtocolStatusTypeDef *status) { status->BusOff = 0; return HAL_OK; }
static inline int HAL_FDCAN_GetErrorCounters(FDCAN_HandleTypeDef *can, FDCAN_ErrorCountersTypeDef *counters) { counters->TxErrorCnt = counters->RxErrorCnt = 0; return HAL_OK; }
static inline int HAL_UART_AbortReceive(UART_HandleTypeDef *uart) { ++test_uart_abort_rx_count; return HAL_OK; }
static inline int HAL_UART_AbortTransmit(UART_HandleTypeDef *uart) { ++test_uart_abort_tx_count; return HAL_OK; }
static inline int HAL_UART_DeInit(UART_HandleTypeDef *uart) { ++test_uart_deinit_count; return HAL_OK; }
static inline int HAL_UART_Init(UART_HandleTypeDef *uart) { ++test_uart_init_count; return HAL_OK; }
static inline int HAL_UARTEx_SetTxFifoThreshold(UART_HandleTypeDef *uart, unsigned threshold) { return HAL_OK; }
static inline int HAL_UARTEx_SetRxFifoThreshold(UART_HandleTypeDef *uart, unsigned threshold) { return HAL_OK; }
static inline int HAL_UARTEx_DisableFifoMode(UART_HandleTypeDef *uart) { return HAL_OK; }
static inline int HAL_UARTEx_ReceiveToIdle_DMA(UART_HandleTypeDef *uart, uint8_t *data, unsigned size) { ++test_uart_rx_start_count; return HAL_OK; }
static inline int HAL_TIM_PWM_Start(TIM_HandleTypeDef *timer, unsigned channel) { return HAL_OK; }
static inline int USBD_CDC_ReceivePacket(USBD_HandleTypeDef *usb) { return USBD_OK; }
int HAL_UART_Transmit_DMA(UART_HandleTypeDef *uart, uint8_t *data, unsigned length);
int CDC_Transmit_FS(uint8_t *data, uint16_t length);
void Error_Handler(void);
#endif

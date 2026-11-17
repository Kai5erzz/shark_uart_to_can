#ifndef BOARD_CONFIG_H
#define BOARD_CONFIG_H

/* User confirmed: 32 MHz crystal, active-low key, active-low LEDs.
 * TJA1044: STB=0 normal; STB=1 standby. */
#define BOARD_KEY_PRESSED GPIO_PIN_RESET
#define BOARD_LED_ON GPIO_PIN_RESET
#define BOARD_LED_OFF GPIO_PIN_SET
/* LED PWM uses TIM_OCPOLARITY_LOW in the IOC: larger pulse = brighter. */
#define BOARD_LED_PWM_MAX 999U
#define BOARD_CAN_STB_NORMAL GPIO_PIN_RESET
#define BOARD_CAN_STB_STANDBY GPIO_PIN_SET
#define BOARD_UART_BAUD 921600U
#define BOARD_UART2_BAUD_DEFAULT 921600U

#endif

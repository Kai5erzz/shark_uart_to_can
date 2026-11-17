#ifndef BRIDGE_UI_H
#define BRIDGE_UI_H
#include <stdint.h>
enum { BRIDGE_UART_CAN, BRIDGE_USB_CAN, BRIDGE_USB_TTL, BRIDGE_MODE_COUNT };
enum { BRIDGE_UI_NONE, BRIDGE_UI_MODE, BRIDGE_UI_ENTER, BRIDGE_UI_COUNT,
       BRIDGE_UI_SAVE, BRIDGE_UI_CANCEL };
typedef struct {
    uint32_t changed, down, last_action;
    uint8_t mode, setting, id, candidate, clicks, raw, pressed;
} BridgeUI;
void Bridge_UIInit(BridgeUI *ui, uint8_t id);
unsigned Bridge_UIUpdate(BridgeUI *ui, uint32_t now, uint8_t pressed);
#endif

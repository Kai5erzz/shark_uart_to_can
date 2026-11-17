#ifndef BRIDGE_APP_H
#define BRIDGE_APP_H
#include <stdint.h>

void Bridge_Run(void);
void Bridge_USBReceive(const uint8_t *data, uint32_t length);
void Bridge_USBReset(void);
void Bridge_USBTransmitComplete(void);
#endif

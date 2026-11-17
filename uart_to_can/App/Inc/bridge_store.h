#ifndef BRIDGE_STORE_H
#define BRIDGE_STORE_H
#include <stdint.h>
#define BRIDGE_STORE_PAGE_SIZE 2048U
#define BRIDGE_STORE_RECORD_SIZE 16U
#define BRIDGE_STORE_BASE 0x0800f000UL

typedef struct {
    const uint8_t *page[2];
    void *context;
    int (*erase)(void *context, unsigned page);
    int (*program)(void *context, unsigned page, unsigned offset, uint64_t data);
    uint32_t sequence;
    int latest_page;
    uint8_t id;
} BridgeStore;
uint8_t Bridge_StoreLoad(BridgeStore *store);
int Bridge_StoreSave(BridgeStore *store, uint8_t id);
uint8_t Bridge_SettingsLoad(void);
int Bridge_SettingsSave(uint8_t id);
#endif

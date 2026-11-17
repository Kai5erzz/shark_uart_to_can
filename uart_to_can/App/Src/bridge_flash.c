#include "bridge_store.h"
#include "main.h"
#include "FreeRTOS.h"
#include "task.h"

static int erase_page(void *context, unsigned page)
{
    (void)context;
    FLASH_EraseInitTypeDef request = {0};
    uint32_t error = 0;
    /* First-bank pages 30/31 are valid on G431 and the observed 0x469 dual-bank die. */
    if ((DBGMCU->IDCODE & 0xfffU) == 0x469U) CLEAR_BIT(FLASH->CR, 1U << 11);
    request.TypeErase = FLASH_TYPEERASE_PAGES;
    request.Banks = FLASH_BANK_1;
    request.Page = 30U + page;
    request.NbPages = 1;
    return HAL_FLASHEx_Erase(&request, &error) == HAL_OK;
}
static int program_word(void *context, unsigned page, unsigned offset, uint64_t data)
{
    (void)context;
    return HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD,
        BRIDGE_STORE_BASE + page * BRIDGE_STORE_PAGE_SIZE + offset, data) == HAL_OK;
}
static BridgeStore store = {
    .page = {(const uint8_t *)BRIDGE_STORE_BASE,
             (const uint8_t *)(BRIDGE_STORE_BASE + BRIDGE_STORE_PAGE_SIZE)},
    .erase = erase_page, .program = program_word
};
uint8_t Bridge_SettingsLoad(void) { return Bridge_StoreLoad(&store); }
int Bridge_SettingsSave(uint8_t id)
{
    /* Single-bank 0x469 uses a different erase size. Never erase it using this map. */
    unsigned die = DBGMCU->IDCODE & 0xfffU;
    if (die != 0x468U && (die != 0x469U || !(FLASH->OPTR & (1U << 22)))) return 0;
    if (HAL_FLASH_Unlock() != HAL_OK) return 0;
    /* Flash programming can stall this bank. Only explicit configuration saves do it;
     * no flash erase/program occurs in the streaming path. IRQs stay enabled so the
     * HAL timeout timebase is alive; UART DMA continues while Flash is busy. */
    int ok = Bridge_StoreSave(&store, id);
    HAL_FLASH_Lock();
    return ok;
}

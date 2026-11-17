#include "bridge_store.h"
#include <string.h>

static uint32_t crc32(const uint8_t *data, unsigned length)
{
    uint32_t crc = 0xffffffffU;
    while (length--) {
        crc ^= *data++;
        for (unsigned b = 0; b < 8; ++b) crc = (crc >> 1) ^ ((crc & 1U) ? 0xedb88320U : 0U);
    }
    return ~crc;
}
static uint32_t word(const uint8_t *p)
{ return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void put(uint8_t *p, uint32_t v)
{ for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (i * 8U)); }
static int blank(const uint8_t *p)
{
    for (unsigned i = 0; i < BRIDGE_STORE_RECORD_SIZE; ++i) if (p[i] != 255) return 0;
    return 1;
}
uint8_t Bridge_StoreLoad(BridgeStore *s)
{
    s->latest_page = -1; s->sequence = 0; s->id = 1;
    for (unsigned page = 0; page < 2; ++page) {
        for (unsigned at = 0; at < BRIDGE_STORE_PAGE_SIZE; at += BRIDGE_STORE_RECORD_SIZE) {
            const uint8_t *p = s->page[page] + at;
            uint32_t sequence = word(p + 4), id = word(p + 8);
            if (word(p) != 0x314b4853U || id < 1 || id > 32 || word(p + 12) != crc32(p, 12)) continue;
            if (s->latest_page < 0 || (int32_t)(sequence - s->sequence) > 0) {
                s->latest_page = (int)page; s->sequence = sequence; s->id = (uint8_t)id;
            }
        }
    }
    return s->id;
}
int Bridge_StoreSave(BridgeStore *s, uint8_t id)
{
    uint8_t record[BRIDGE_STORE_RECORD_SIZE];
    if (id < 1 || id > 32 || !s->erase || !s->program) return 0;
    Bridge_StoreLoad(s);
    if (s->latest_page >= 0 && s->id == id) return 1;
    unsigned page = s->latest_page < 0 ? 0U : (unsigned)s->latest_page;
    unsigned at;
    for (at = 0; at < BRIDGE_STORE_PAGE_SIZE; at += BRIDGE_STORE_RECORD_SIZE)
        if (blank(s->page[page] + at)) break;
    if (at == BRIDGE_STORE_PAGE_SIZE) {
        page = s->latest_page < 0 ? 0U : page ^ 1U;
        if (!s->erase(s->context, page)) return 0;
        at = 0;
    }
    uint32_t sequence = s->sequence + 1U;
    put(record, 0x314b4853U); put(record + 4, sequence); put(record + 8, id);
    put(record + 12, crc32(record, 12));
    for (unsigned i = 0; i < sizeof(record); i += 8) {
        uint64_t value;
        memcpy(&value, record + i, 8);
        if (!s->program(s->context, page, at + i, value)) return 0;
    }
    Bridge_StoreLoad(s);
    return s->id == id && s->sequence == sequence;
}

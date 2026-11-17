#include "bridge_protocol.h"
#include "bridge_ring.h"
#include "bridge_store.h"
#include "bridge_ui.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t random_state = 0x81234567;
static uint32_t rnd(void)
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}
static void binary(void)
{
    assert(Bridge_CRC16((const uint8_t *)"123456789", 9) == 0x29b1);
    BridgeDecoder d = {0};
    BridgePacket got;
    uint8_t wire[80];
    for (unsigned round = 0; round < 3000; ++round) {
        BridgePacket p = {.id = rnd(), .sequence = (uint16_t)rnd(),
            .type = (uint8_t)rnd(), .flags = (uint8_t)rnd(), .length = round % 65};
        for (unsigned j = 0; j < p.length; ++j) p.data[j] = (uint8_t)(round + j);
        size_t n = Bridge_Encode(&p, wire, sizeof(wire));
        assert(n == p.length + 16U);
        assert(!Bridge_Encode(&p, wire, n - 1));
        assert(Bridge_Decode(wire + 2, n - 3, &got));
        assert(got.id == p.id && got.sequence == p.sequence && got.type == p.type && got.flags == p.flags);
        assert(got.length == p.length && !memcmp(got.data, p.data, p.length));
        for (size_t at = 0; at < n; ++at)
            assert(Bridge_Feed(&d, wire[at], &got) == (at == n - 1 ? BRIDGE_DECODE_PACKET : BRIDGE_DECODE_NONE));
        /* Every single-bit corruption of the COBS body must fail. */
        if (round < 65) for (size_t at = 2; at < n - 1; ++at) for (unsigned b = 0; b < 8; ++b) {
            wire[at] ^= (uint8_t)(1U << b);
            assert(!Bridge_Decode(wire + 2, n - 3, &got));
            wire[at] ^= (uint8_t)(1U << b);
        }
    }
    Bridge_DecoderReset(&d);
    const char *text = "ping\r\necho a\bB\n";
    unsigned lines = 0;
    for (unsigned i = 0; text[i]; ++i) if (Bridge_Feed(&d, text[i], &got) == BRIDGE_DECODE_LINE) {
        assert(!strcmp(d.line, lines++ ? "echo B" : "ping"));
    }
    assert(lines == 2);
    for (unsigned i = 0; i < 300; ++i) Bridge_Feed(&d, 'x', &got);
    assert(Bridge_Feed(&d, '\n', &got) == BRIDGE_DECODE_ERROR);
    Bridge_Feed(&d, 0, &got); Bridge_Feed(&d, 0xa5, &got);
    for (unsigned i = 0; i < 90; ++i) Bridge_Feed(&d, 1, &got);
    assert(Bridge_Feed(&d, 0, &got) == BRIDGE_DECODE_ERROR);
    BridgePacket p = {.type = BRIDGE_ECHO, .length = 6, .data = {0,0xa5,'\r','\n',0xff,0}};
    size_t n = Bridge_Encode(&p, wire, sizeof(wire));
    for (size_t at = 0; at < n; ++at)
        assert(Bridge_Feed(&d, wire[at], &got) == (at + 1 == n ? BRIDGE_DECODE_PACKET : BRIDGE_DECODE_NONE));
    text = "ping\n";
    for (unsigned i = 0; text[i]; ++i)
        assert(Bridge_Feed(&d, text[i], &got) == (text[i] == '\n' ? BRIDGE_DECODE_LINE : BRIDGE_DECODE_NONE));
    assert(!strcmp(d.line, "ping"));
    /* Noise/partial frames must never prevent resynchronizing at a new prefix. */
    for (unsigned round = 0; round < 1000; ++round) {
        for (unsigned j = 0; j < round % 200; ++j) Bridge_Feed(&d, (uint8_t)rnd(), &got);
        for (size_t at = 0; at < n; ++at) {
            int event = Bridge_Feed(&d, wire[at], &got);
            if (at + 1 == n) assert(event == BRIDGE_DECODE_PACKET);
        }
    }
    p.length = 65;
    assert(!Bridge_Encode(&p, wire, sizeof(wire)));
    assert(!Bridge_Decode(NULL, 0, &got));
}
static void ring(void)
{
    BridgeRing r = {0};
    unsigned sent = 0, received = 0;
    uint8_t data[1500], out[1500];
    for (unsigned round = 0; round < 10000; ++round) {
        unsigned n = rnd() % sizeof(data);
        for (unsigned i = 0; i < n; ++i) data[i] = (uint8_t)(sent + i);
        unsigned expected = n < Bridge_RingFree(&r) ? n : Bridge_RingFree(&r);
        assert(Bridge_RingWrite(&r, data, n) == expected);
        sent += expected;
        n = rnd() % sizeof(out);
        expected = n < Bridge_RingCount(&r) ? n : Bridge_RingCount(&r);
        assert(Bridge_RingRead(&r, out, n) == expected);
        for (unsigned i = 0; i < expected; ++i) assert(out[i] == (uint8_t)(received + i));
        received += expected;
        assert(sent - received == Bridge_RingCount(&r));
    }
}
static unsigned press(BridgeUI *u, uint32_t *now, unsigned duration)
{
    assert(duration >= 25);
    Bridge_UIUpdate(u, *now, 1);
    Bridge_UIUpdate(u, *now + 25U, 1);
    Bridge_UIUpdate(u, *now + duration, 0);
    unsigned event = Bridge_UIUpdate(u, *now + duration + 25U, 0);
    *now += duration + 100U;
    return event;
}
static void button(void)
{
    BridgeUI u;
    uint32_t now = 100;
    Bridge_UIInit(&u, 7);
    assert(press(&u, &now, 999) == BRIDGE_UI_NONE && u.mode == 0);
    assert(press(&u, &now, 1000) == BRIDGE_UI_MODE && u.mode == 1);
    assert(press(&u, &now, 2999) == BRIDGE_UI_MODE && u.mode == 2);
    assert(press(&u, &now, 1000) == BRIDGE_UI_MODE && u.mode == 0);
    assert(press(&u, &now, 3000) == BRIDGE_UI_ENTER && u.setting && u.candidate == 7);
    assert(press(&u, &now, 1000) == BRIDGE_UI_CANCEL && !u.setting && u.id == 7);
    assert(press(&u, &now, 3000) == BRIDGE_UI_ENTER);
    for (unsigned i = 1; i <= 33; ++i) {
        assert(press(&u, &now, 60) == BRIDGE_UI_COUNT);
        assert(u.candidate == (i - 1) % 32 + 1 && u.id == 7);
    }
    assert(press(&u, &now, 1000) == BRIDGE_UI_SAVE && u.setting);
    /* Application commits/exits only after successful NVM save. */
    u.setting = 0;
    assert(press(&u, &now, 3000) == BRIDGE_UI_ENTER);
    assert(Bridge_UIUpdate(&u, now + 30000U, 0) == BRIDGE_UI_CANCEL && !u.setting);
    Bridge_UIInit(&u, 32); now = 0xfffff000U;
    assert(press(&u, &now, 3000) == BRIDGE_UI_ENTER);
    assert(press(&u, &now, 60) == BRIDGE_UI_COUNT && u.candidate == 1);
    assert(press(&u, &now, 1000) == BRIDGE_UI_SAVE);
    Bridge_UIInit(&u, 1);
    Bridge_UIUpdate(&u, 0, 1); Bridge_UIUpdate(&u, 10, 0);
    Bridge_UIUpdate(&u, 15, 1); Bridge_UIUpdate(&u, 30, 0);
    assert(Bridge_UIUpdate(&u, 60, 0) == BRIDGE_UI_NONE && !u.pressed);
}
typedef struct { uint8_t pages[2][2048]; int fail_program, fail_erase, byte_budget; unsigned programs, erases; } Flash;
static int erase(void *context, unsigned page)
{
    Flash *f = context;
    assert(page < 2);
    ++f->erases;
    if (f->fail_erase) { memset(f->pages[page], 255, 1024); return 0; }
    memset(f->pages[page], 255, 2048); return 1;
}
static int program(void *context, unsigned page, unsigned at, uint64_t value)
{
    Flash *f = context;
    assert(page < 2 && at <= 2040 && at % 8 == 0);
    unsigned call = f->programs++;
    if (f->fail_program >= 0 && (int)call == f->fail_program) return 0;
    uint8_t bytes[8]; memcpy(bytes, &value, 8);
    for (unsigned i = 0; i < 8; ++i) {
        if (!f->byte_budget) return 0;
        if (f->byte_budget > 0) --f->byte_budget;
        assert(f->pages[page][at + i] == 255);
        f->pages[page][at + i] &= bytes[i];
    }
    return 1;
}
static BridgeStore make_store(Flash *f)
{
    return (BridgeStore){.page = {f->pages[0], f->pages[1]}, .context = f, .erase = erase, .program = program};
}
static void fixture_record(uint8_t *record, uint32_t sequence, uint8_t id)
{
    const uint32_t fields[] = {0x314b4853U, sequence, id};
    for (unsigned field = 0; field < 3; ++field)
        for (unsigned j = 0; j < 4; ++j) record[field * 4 + j] = (uint8_t)(fields[field] >> (j * 8U));
    uint32_t crc = 0xffffffffU;
    for (unsigned i = 0; i < 12; ++i) {
        crc ^= record[i];
        for (unsigned b = 0; b < 8; ++b) crc = crc >> 1 ^ (crc & 1 ? 0xedb88320U : 0U);
    }
    crc = ~crc;
    for (unsigned j = 0; j < 4; ++j) record[12 + j] = (uint8_t)(crc >> (j * 8U));
}
static void store(void)
{
    Flash f; memset(&f, 255, sizeof(f)); f.programs = f.erases = 0; f.fail_program = -1; f.fail_erase = 0;
    BridgeStore s = make_store(&f);
    assert(Bridge_StoreLoad(&s) == 1 && s.latest_page == -1);
    assert(!Bridge_StoreSave(&s, 0) && !Bridge_StoreSave(&s, 33));
    assert(Bridge_StoreSave(&s, 32));
    unsigned programs = f.programs;
    assert(Bridge_StoreSave(&s, 32) && f.programs == programs);
    Flash snapshot = f;
    /* Power loss at every byte of a new record, including inside a doubleword. */
    for (int cut = 0; cut < 16; ++cut) {
        f = snapshot; f.byte_budget = cut;
        assert(!Bridge_StoreSave(&s, 2));
        s = make_store(&f); assert(Bridge_StoreLoad(&s) == 32);
        f.byte_budget = -1;
        assert(Bridge_StoreSave(&s, 2));
    }
    for (int cut = 0; cut < 2; ++cut) {
        f = snapshot; f.fail_program = cut; f.programs = 0;
        assert(!Bridge_StoreSave(&s, 2));
        s = make_store(&f); assert(Bridge_StoreLoad(&s) == 32);
        f.fail_program = -1;
        assert(Bridge_StoreSave(&s, 2));
        s = make_store(&f); assert(Bridge_StoreLoad(&s) == 2);
    }
    f = snapshot; s = make_store(&f);
    for (unsigned i = 0; i < 127; ++i) assert(Bridge_StoreSave(&s, (uint8_t)(i % 31 + 1)));
    assert(s.sequence == 128 && s.latest_page == 0);
    snapshot = f;
    for (int cut = 0; cut < 3; ++cut) {
        f = snapshot; s = make_store(&f); f.programs = 0;
        f.fail_erase = cut == 2; f.fail_program = cut == 2 ? -1 : cut;
        assert(!Bridge_StoreSave(&s, 32));
        s = make_store(&f); assert(Bridge_StoreLoad(&s) == 3);
        f.fail_erase = 0; f.fail_program = -1;
        assert(Bridge_StoreSave(&s, 32));
        assert(Bridge_StoreLoad(&s) == 32);
    }
    /* Corrupt newest record: previous committed record remains recoverable. */
    f = snapshot; s = make_store(&f);
    f.pages[0][127 * 16 + 12] ^= 1;
    assert(Bridge_StoreLoad(&s) == 2);
    for (unsigned i = 0; i < 1000; ++i) {
        uint8_t id = (uint8_t)(i % 32 + 1);
        assert(Bridge_StoreSave(&s, id));
        s = make_store(&f); assert(Bridge_StoreLoad(&s) == id);
    }
    assert(f.erases >= 5);
    memset(f.pages, 255, sizeof(f.pages));
    fixture_record(f.pages[0], 0xfffffffeU, 7);
    s = make_store(&f);
    assert(Bridge_StoreLoad(&s) == 7);
    assert(Bridge_StoreSave(&s, 8) && s.sequence == 0xffffffffU);
    assert(Bridge_StoreSave(&s, 9) && s.sequence == 0);
    s = make_store(&f);
    assert(Bridge_StoreLoad(&s) == 9);
}
int main(void)
{
    binary(); ring(); button(); store();
    puts("PASS product: binary CRC/mux/recovery, ring wrap, button timing/count/wrap, Flash power-loss/rotation");
}

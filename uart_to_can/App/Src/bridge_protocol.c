#include "bridge_protocol.h"
#include <stdio.h>
#include <string.h>

static int hex(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int Bridge_ParseFrame(const char *line, BridgeFrame *frame)
{
    BridgeFrame value = {0};
    const char *p;
    unsigned digits = 0;
    if (!line || !frame) return 0;
    if (strncmp(line, "sendx ", 6) == 0) {
        value.extended = 1;
        p = line + 6;
    } else if (strncmp(line, "send ", 5) == 0) {
        p = line + 5;
    } else return 0;
    while (*p == ' ') ++p;
    while (hex(*p) >= 0) {
        if (++digits > 8) return 0;
        value.id = (value.id << 4) | (uint32_t)hex(*p++);
    }
    if (!digits || *p++ != ' ' || value.id > (value.extended ? 0x1fffffffU : 0x7ffU)) return 0;
    while (*p == ' ') ++p;
    if (*p == '-') {
        ++p;
    } else {
        while (*p) {
            int high = hex(*p++);
            if (high < 0 || hex(*p) < 0 || value.length == 8) return 0;
            value.data[value.length++] = (uint8_t)((high << 4) | hex(*p++));
            while (*p == ' ') ++p;
        }
        if (!value.length) return 0;
    }
    while (*p == ' ') ++p;
    if (*p) return 0;
    *frame = value;
    return 1;
}

int Bridge_FormatFrame(const BridgeFrame *frame, char *out, size_t capacity)
{
    int length;
    if (!frame || !out || capacity < 40 || frame->length > 8 ||
        frame->id > (frame->extended ? 0x1fffffffU : 0x7ffU)) return 0;
    length = snprintf(out, capacity, "CAN %c %08lX %u ", frame->extended ? 'X' : 'S',
                      (unsigned long)frame->id, frame->length);
    for (unsigned i = 0; i < frame->length; ++i)
        length += snprintf(out + length, capacity - (size_t)length, "%02X", frame->data[i]);
    length += snprintf(out + length, capacity - (size_t)length, "\r\n");
    return length;
}

uint16_t Bridge_CRC16(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xffffU;
    while (length--) {
        crc ^= (uint16_t)*data++ << 8;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (uint16_t)((crc << 1) ^ ((crc & 0x8000U) ? 0x1021U : 0U));
    }
    return crc;
}

size_t Bridge_Encode(const BridgePacket *p, uint8_t *out, size_t capacity)
{
    uint8_t raw[12 + BRIDGE_PAYLOAD_MAX];
    if (!p || !out || p->length > BRIDGE_PAYLOAD_MAX || capacity < (size_t)p->length + 16U) return 0;
    raw[0] = 1; raw[1] = p->type; raw[2] = p->flags; raw[3] = p->length;
    raw[4] = (uint8_t)p->sequence; raw[5] = (uint8_t)(p->sequence >> 8);
    for (unsigned i = 0; i < 4; ++i) raw[6 + i] = (uint8_t)(p->id >> (8U * i));
    memcpy(raw + 10, p->data, p->length);
    uint16_t crc = Bridge_CRC16(raw, 10U + p->length);
    raw[10 + p->length] = (uint8_t)crc;
    raw[11 + p->length] = (uint8_t)(crc >> 8);
    out[0] = 0; out[1] = 0xa5;
    size_t write = 3, code_index = 2;
    uint8_t code = 1;
    for (unsigned i = 0; i < 12U + p->length; ++i) {
        if (!raw[i]) {
            out[code_index] = code;
            code_index = write++;
            code = 1;
        } else { out[write++] = raw[i]; ++code; }
    }
    out[code_index] = code;
    out[write++] = 0;
    return write;
}

int Bridge_Decode(const uint8_t *encoded, size_t length, BridgePacket *p)
{
    uint8_t raw[12 + BRIDGE_PAYLOAD_MAX];
    size_t read = 0, write = 0;
    if (!encoded || !p || !length || length > sizeof(raw) + 1U) return 0;
    while (read < length) {
        unsigned code = encoded[read++];
        if (!code || read + code - 1U > length || write + code - 1U > sizeof(raw)) return 0;
        for (unsigned i = 1; i < code; ++i) {
            if (!encoded[read]) return 0;
            raw[write++] = encoded[read++];
        }
        if (code != 255U && read < length) {
            if (write == sizeof(raw)) return 0;
            raw[write++] = 0;
        }
    }
    if (write < 12 || raw[0] != 1 || raw[3] > BRIDGE_PAYLOAD_MAX || write != 12U + raw[3]) return 0;
    uint16_t crc = (uint16_t)(raw[write - 2] | ((uint16_t)raw[write - 1] << 8));
    if (crc != Bridge_CRC16(raw, write - 2)) return 0;
    p->type = raw[1]; p->flags = raw[2]; p->length = raw[3];
    p->sequence = (uint16_t)(raw[4] | ((uint16_t)raw[5] << 8));
    p->id = 0;
    for (unsigned i = 0; i < 4; ++i) p->id |= (uint32_t)raw[6 + i] << (8U * i);
    memcpy(p->data, raw + 10, p->length);
    return 1;
}

void Bridge_DecoderReset(BridgeDecoder *d)
{ d->used = 0; d->state = 0; }

int Bridge_Feed(BridgeDecoder *d, uint8_t byte, BridgePacket *packet)
{
    /* state: ASCII=0, magic=1, binary=2, bad ASCII=3, bad binary=4 */
    if (byte == 0) {
        int result = BRIDGE_DECODE_NONE;
        if (d->state == 2 && d->used)
            result = Bridge_Decode(d->encoded, d->used, packet) ? BRIDGE_DECODE_PACKET : BRIDGE_DECODE_ERROR;
        else if (d->state == 4) result = BRIDGE_DECODE_ERROR;
        d->used = 0; d->state = 1;
        return result;
    }
    if (d->state == 1) {
        d->used = 0;
        d->state = byte == 0xa5 ? 2 : 0;
        if (d->state == 2) return BRIDGE_DECODE_NONE;
    }
    if (d->state == 2) {
        if (d->used < sizeof(d->encoded)) d->encoded[d->used++] = byte;
        else d->state = 4;
    } else if (d->state == 4) {
        return BRIDGE_DECODE_NONE;
    } else if (byte == '\r' || byte == '\n') {
        int result = d->state == 3 ? BRIDGE_DECODE_ERROR : d->used ? BRIDGE_DECODE_LINE : BRIDGE_DECODE_NONE;
        d->line[d->used] = 0;
        d->used = 0; d->state = 0;
        return result;
    } else if (d->state == 3) {
        return BRIDGE_DECODE_NONE;
    } else if (byte == '\b' || byte == 127) {
        if (d->used) --d->used;
    } else if (byte >= 32 && byte < 127 && d->used < sizeof(d->line) - 1U) {
        d->line[d->used++] = (char)byte;
    } else d->state = 3;
    return BRIDGE_DECODE_NONE;
}

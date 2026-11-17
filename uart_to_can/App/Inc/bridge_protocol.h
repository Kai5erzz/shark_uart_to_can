#ifndef BRIDGE_PROTOCOL_H
#define BRIDGE_PROTOCOL_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t id;
    uint8_t extended;
    uint8_t length;
    uint8_t data[8];
} BridgeFrame;

/* send <hex id> <0..8 hex bytes>, sendx <hex id> <hex bytes>.
 * '-' denotes an empty data frame; remote and CAN FD frames are unsupported. */
int Bridge_ParseFrame(const char *line, BridgeFrame *frame);
int Bridge_FormatFrame(const BridgeFrame *frame, char *out, size_t capacity);

#define BRIDGE_PAYLOAD_MAX 64U
#define BRIDGE_PACKET_MAX 80U
#define BRIDGE_LINE_MAX 192U
#define BRIDGE_FLAG_EXT 1U
#define BRIDGE_FLAG_ACK 2U
#define BRIDGE_FLAG_DEFAULT_ID 4U
enum {
    BRIDGE_CAN_TX = 1, BRIDGE_STREAM_TX = 2, BRIDGE_ECHO = 0x10,
    BRIDGE_INFO = 0x20, BRIDGE_MODE = 0x21, BRIDGE_ID = 0x22,
    BRIDGE_SAVE = 0x23, BRIDGE_CAN_TEST = 0x24,
    BRIDGE_ACK = 0x81, BRIDGE_CAN_RX = 0x82,
    BRIDGE_ECHO_REPLY = 0x90, BRIDGE_INFO_REPLY = 0xa0
};
typedef struct {
    uint32_t id;
    uint16_t sequence;
    uint8_t type, flags, length;
    uint8_t data[BRIDGE_PAYLOAD_MAX];
} BridgePacket;
typedef struct {
    char line[BRIDGE_LINE_MAX];
    uint8_t encoded[BRIDGE_PACKET_MAX];
    uint16_t used;
    uint8_t state;
} BridgeDecoder;
enum { BRIDGE_DECODE_NONE, BRIDGE_DECODE_LINE, BRIDGE_DECODE_PACKET, BRIDGE_DECODE_ERROR };

uint16_t Bridge_CRC16(const uint8_t *data, size_t length);
size_t Bridge_Encode(const BridgePacket *packet, uint8_t *out, size_t capacity);
int Bridge_Decode(const uint8_t *encoded, size_t length, BridgePacket *packet);
/* Streaming mux: 00 A5 COBS(packet) 00 or CR/LF-terminated ASCII commands.
 * The returned ASCII line stays valid until the next call. */
int Bridge_Feed(BridgeDecoder *decoder, uint8_t byte, BridgePacket *packet);
void Bridge_DecoderReset(BridgeDecoder *decoder);
#endif

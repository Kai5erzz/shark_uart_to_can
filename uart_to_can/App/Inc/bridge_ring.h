#ifndef BRIDGE_RING_H
#define BRIDGE_RING_H
#include <stdint.h>
#include <string.h>

#define BRIDGE_RING_SIZE 1024U
typedef struct {
    uint8_t bytes[BRIDGE_RING_SIZE];
    volatile uint16_t head, tail;
} BridgeRing;

/* SPSC. Cortex-M publishes bytes before the index; no DMA uses this storage. */
#if defined(__arm__) || defined(__thumb__)
#define BRIDGE_BARRIER() __asm volatile ("dmb" ::: "memory")
#else
#define BRIDGE_BARRIER() __atomic_thread_fence(__ATOMIC_SEQ_CST)
#endif
static inline unsigned Bridge_RingCount(const BridgeRing *r)
{ return (r->head - r->tail) & (BRIDGE_RING_SIZE - 1U); }
static inline unsigned Bridge_RingFree(const BridgeRing *r)
{ return BRIDGE_RING_SIZE - 1U - Bridge_RingCount(r); }
static inline unsigned Bridge_RingWrite(BridgeRing *r, const uint8_t *data, unsigned n)
{
    unsigned free = Bridge_RingFree(r), head = r->head;
    if (n > free) n = free;
    unsigned first = BRIDGE_RING_SIZE - head;
    if (first > n) first = n;
    memcpy(r->bytes + head, data, first);
    memcpy(r->bytes, data + first, n - first);
    BRIDGE_BARRIER();
    r->head = (uint16_t)((head + n) & (BRIDGE_RING_SIZE - 1U));
    return n;
}
static inline unsigned Bridge_RingRead(BridgeRing *r, uint8_t *out, unsigned n)
{
    unsigned count = Bridge_RingCount(r), tail = r->tail;
    if (n > count) n = count;
    unsigned first = BRIDGE_RING_SIZE - tail;
    if (first > n) first = n;
    BRIDGE_BARRIER();
    memcpy(out, r->bytes + tail, first);
    memcpy(out + first, r->bytes, n - first);
    BRIDGE_BARRIER();
    r->tail = (uint16_t)((tail + n) & (BRIDGE_RING_SIZE - 1U));
    return n;
}
#endif

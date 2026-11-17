#include "bridge_protocol.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    BridgeFrame f;
    char line[48];
    assert(Bridge_ParseFrame("send 123 55AA000102030405", &f));
    assert(f.id == 0x123 && f.length == 8 && f.data[1] == 0xaa && !f.extended);
    assert(Bridge_FormatFrame(&f, line, sizeof(line)));
    assert(!strcmp(line, "CAN S 00000123 8 55AA000102030405\r\n"));
    assert(Bridge_ParseFrame("sendx 1fffffff FF 00 7f", &f));
    assert(f.extended && f.id == 0x1fffffff && f.length == 3 && f.data[2] == 0x7f);
    assert(Bridge_ParseFrame("send 0 -", &f) && f.length == 0);
    assert(!Bridge_ParseFrame("send 800 00", &f));
    assert(!Bridge_ParseFrame("sendx 20000000 00", &f));
    assert(!Bridge_ParseFrame("sendx FFFFFFFFF 00", &f));
    assert(!Bridge_ParseFrame("send 123 123", &f));
    assert(!Bridge_ParseFrame("send 123 000102030405060708", &f));
    assert(!Bridge_ParseFrame("send 123 -a", &f));
    assert(!Bridge_ParseFrame("send 123 ", &f));
    assert(!Bridge_ParseFrame("send 123", &f));
    assert(!Bridge_ParseFrame("send 123 0G", &f));
    assert(!Bridge_ParseFrame("send 123 0 0", &f));
    assert(!Bridge_ParseFrame("send 123\t00", &f));
    assert(!Bridge_ParseFrame("sendx 1 00garbage", &f));
    assert(!Bridge_ParseFrame(NULL, &f));
    assert(!Bridge_FormatFrame(&f, line, 2));
    puts("PASS protocol: standard/extended, empty frames, bounds and malformed input");
}

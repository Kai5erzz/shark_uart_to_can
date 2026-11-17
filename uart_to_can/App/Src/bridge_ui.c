#include "bridge_ui.h"
#include <string.h>
void Bridge_UIInit(BridgeUI *ui, uint8_t id)
{
    memset(ui, 0, sizeof(*ui));
    ui->id = id; ui->candidate = id;
}
unsigned Bridge_UIUpdate(BridgeUI *u, uint32_t now, uint8_t raw)
{
    if (raw != u->raw) { u->raw = raw; u->changed = now; }
    if (u->pressed != raw && (uint32_t)(now - u->changed) >= 25U) {
        u->pressed = raw;
        u->last_action = now;
        if (raw) u->down = now;
        else {
            uint32_t held = now - u->down;
            if (u->setting) {
                if (held >= 1000U) {
                    if (!u->clicks) { u->setting = 0; return BRIDGE_UI_CANCEL; }
                    return BRIDGE_UI_SAVE;
                }
                u->clicks = (uint8_t)(u->clicks % 32U + 1U);
                u->candidate = u->clicks;
                return BRIDGE_UI_COUNT;
            }
            if (held >= 3000U) {
                u->setting = 1; u->candidate = u->id; u->clicks = 0;
                return BRIDGE_UI_ENTER;
            }
            if (held >= 1000U) {
                u->mode = (uint8_t)((u->mode + 1U) % BRIDGE_MODE_COUNT);
                return BRIDGE_UI_MODE;
            }
        }
    }
    if (u->setting && !u->pressed && !u->raw && (uint32_t)(now - u->last_action) >= 30000U) {
        u->setting = 0;
        return BRIDGE_UI_CANCEL;
    }
    return BRIDGE_UI_NONE;
}

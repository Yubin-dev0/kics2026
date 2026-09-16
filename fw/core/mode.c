/* mode.c - see mode.h */
#include "mode.h"

void mode_init(mode_ctx_t *m)
{
    m->mode = MODE_EDGE;
    m->n_sw = 0;
}

bool mode_apply(mode_ctx_t *m, uint8_t req)
{
    if (req == m->mode) return false;
    m->mode = req;
    m->n_sw++;
    return true;
}

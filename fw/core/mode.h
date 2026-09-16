/* mode.h - level-triggered edge/local mode switch with a switch counter. */
#ifndef MODE_H
#define MODE_H

#include <stdbool.h>
#include <stdint.h>

#define MODE_EDGE 0
#define MODE_LOCAL 1

typedef struct {
    uint8_t mode;
    uint16_t n_sw;
} mode_ctx_t;

void mode_init(mode_ctx_t *m);

/* Applies the requested mode. Returns true only when the mode actually changed. */
bool mode_apply(mode_ctx_t *m, uint8_t req);

#endif

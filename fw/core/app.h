/* app.h - platform-independent N2 application: one S line in, one C line out.
 * The same code runs on the STM32 (fw/stm32) and on the host (fw/host/fake_stm32). */
#ifndef APP_H
#define APP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mode.h"
#include "proto.h"

#define APP_WDOG_MS 150 /* provisional: 3 missed 20 Hz lines; 0.22 m/s * 150 ms = 33 mm < d_stop - d_col */

typedef struct {
    uint32_t (*cyc_now)(void); /* free-running cycle counter (DWT CYCCNT on target) */
    uint32_t cyc_per_us;       /* SystemCoreClock / 1e6 on target */
    const char *build_id;
    uint32_t sysclk_hz;
} app_cfg_t;

typedef struct {
    app_cfg_t cfg;
    mode_ctx_t m;
    uint32_t last_seq;
    uint32_t last_rx_ms;
    uint16_t bad_lines;
    bool have_rx;
    bool wdog_tripped;
} app_t;

void app_init(app_t *a, const app_cfg_t *cfg);

/* Handles one received line (without '\n'). t_rx_cyc is the cycle count captured
 * when the line's last byte was delivered. Writes the reply into out; returns its length
 * (0 = no reply, e.g. a rejected line). */
size_t app_on_line(app_t *a, const char *line, size_t len, uint32_t t_rx_cyc,
                   uint32_t now_ms, char *out, size_t cap);

/* Counts a line dropped before parsing (overlong, queue full, UART error). */
void app_count_bad(app_t *a);

/* Call periodically. Emits one unsolicited WDOG C line when S lines stop arriving. */
size_t app_poll(app_t *a, uint32_t now_ms, char *out, size_t cap);

#endif

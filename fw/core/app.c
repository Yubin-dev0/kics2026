/* app.c - see app.h */
#include "app.h"

#include "safety.h"

void app_init(app_t *a, const app_cfg_t *cfg)
{
    a->cfg = *cfg;
    mode_init(&a->m);
    a->last_seq = 0;
    a->last_rx_ms = 0;
    a->bad_lines = 0;
    a->have_rx = false;
    a->wdog_tripped = false;
}

void app_count_bad(app_t *a)
{
    if (a->bad_lines < 0xFFFF) a->bad_lines++;
}

size_t app_on_line(app_t *a, const char *line, size_t len, uint32_t t_rx_cyc,
                   uint32_t now_ms, char *out, size_t cap)
{
    /* version query: "V*56" */
    if (len == 4 && line[0] == 'V' && line[1] == '*') {
        return proto_format_v(a->cfg.build_id, a->cfg.sysclk_hz, out, cap);
    }

    s_line_t s;
    if (proto_parse_s(line, len, &s) != PARSE_OK) {
        app_count_bad(a);
        return 0;
    }

    uint32_t switch_us = 0;
    if (mode_apply(&a->m, s.flag)) {
        uint32_t t1 = a->cfg.cyc_now(); /* mode is reflected from here on */
        switch_us = (t1 - t_rx_cyc) / a->cfg.cyc_per_us; /* unsigned wrap is safe */
        if (switch_us == 0) switch_us = 1; /* 0 is reserved for "no switch on this line" */
    }

    int16_t v_safe;
    safety_state_t st = safety_eval(s.min_mm, &v_safe);

    c_line_t c;
    c.seq = s.seq;
    c.mode = a->m.mode;
    c.state = (uint8_t)st; /* reported in both modes, applied only in local mode */
    if (a->m.mode == MODE_LOCAL) {
        c.v_out = v_safe;
        c.w_out = s.local_w;
    } else {
        c.v_out = s.edge_v;
        c.w_out = s.edge_w;
    }
    c.switch_us = switch_us;
    c.n_sw = a->m.n_sw;
    c.bad_lines = a->bad_lines;

    a->last_seq = s.seq;
    a->last_rx_ms = now_ms;
    a->have_rx = true;
    a->wdog_tripped = false;

    return proto_format_c(&c, out, cap);
}

size_t app_poll(app_t *a, uint32_t now_ms, char *out, size_t cap)
{
    if (!a->have_rx || a->wdog_tripped) return 0;
    if ((uint32_t)(now_ms - a->last_rx_ms) <= APP_WDOG_MS) return 0;

    a->wdog_tripped = true;
    c_line_t c;
    c.seq = a->last_seq;
    c.v_out = 0;
    c.w_out = 0;
    c.mode = a->m.mode;
    c.state = ST_WDOG;
    c.switch_us = 0;
    c.n_sw = a->m.n_sw;
    c.bad_lines = a->bad_lines;
    return proto_format_c(&c, out, cap);
}

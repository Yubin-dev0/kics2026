/* proto.h - N1 <-> N2 ASCII line protocol (see fw/PROTOCOL.md). No HAL dependency. */
#ifndef PROTO_H
#define PROTO_H

#include <stddef.h>
#include <stdint.h>

#define PROTO_VERSION 1
#define PROTO_LINE_MAX 96 /* longest legal line incl. checksum, without '\n' */

typedef struct {
    uint32_t seq;
    uint16_t min_mm;  /* 0 = all rays NaN (fail-safe STOP), 65535 = no return (far) */
    int16_t local_w;  /* mrad/s, from the N1 waypoint follower */
    int16_t edge_v;   /* mm/s */
    int16_t edge_w;   /* mrad/s */
    uint8_t flag;     /* requested mode: 0 = edge, 1 = local (level, not event) */
} s_line_t;

typedef struct {
    uint32_t seq;
    int16_t v_out;
    int16_t w_out;
    uint8_t mode;
    uint8_t state;
    uint32_t switch_us;
    uint16_t n_sw;
    uint16_t bad_lines;
} c_line_t;

typedef enum {
    PARSE_OK = 0,
    PARSE_ERR_FORMAT,
    PARSE_ERR_CHECKSUM,
    PARSE_ERR_RANGE
} parse_err_t;

uint8_t proto_checksum(const char *s, size_t n);

/* line: without trailing '\n' or '\r'. Returns PARSE_OK and fills out on success. */
parse_err_t proto_parse_s(const char *line, size_t len, s_line_t *out);

/* Writes "C,...*XX\n". Returns bytes written (0 if cap too small). */
size_t proto_format_c(const c_line_t *c, char *buf, size_t cap);

/* Writes "V,<proto>,<build_id>,<sysclk_hz>*XX\n". */
size_t proto_format_v(const char *build_id, uint32_t sysclk_hz, char *buf, size_t cap);

#endif

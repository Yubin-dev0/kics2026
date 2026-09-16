/* proto.c - parser and formatter for the N1 <-> N2 line protocol. */
#include "proto.h"

#include <stdio.h>

uint8_t proto_checksum(const char *s, size_t n)
{
    uint8_t x = 0;
    for (size_t i = 0; i < n; i++) {
        x ^= (uint8_t)s[i];
    }
    return x;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* Parses a signed decimal in [p, end). Rejects empty fields and overflow past 64-bit guard. */
static int parse_int(const char *p, const char *end, int64_t *out)
{
    int neg = 0;
    int64_t v = 0;
    if (p >= end) return 0;
    if (*p == '-') {
        neg = 1;
        p++;
        if (p >= end) return 0;
    }
    for (; p < end; p++) {
        if (*p < '0' || *p > '9') return 0;
        v = v * 10 + (*p - '0');
        if (v > 0xFFFFFFFFLL) return 0;
    }
    *out = neg ? -v : v;
    return 1;
}

parse_err_t proto_parse_s(const char *line, size_t len, s_line_t *out)
{
    /* locate '*' */
    size_t star = len;
    for (size_t i = 0; i < len; i++) {
        if (line[i] == '*') {
            star = i;
            break;
        }
    }
    if (star == len || star + 3 != len) return PARSE_ERR_FORMAT;
    if (star < 2 || line[0] != 'S' || line[1] != ',') return PARSE_ERR_FORMAT;

    int h1 = hexval(line[star + 1]);
    int h2 = hexval(line[star + 2]);
    if (h1 < 0 || h2 < 0) return PARSE_ERR_FORMAT;
    if (proto_checksum(line, star) != (uint8_t)((h1 << 4) | h2)) return PARSE_ERR_CHECKSUM;

    /* split the 6 fields after "S," */
    int64_t f[6];
    const char *p = line + 2;
    const char *end = line + star;
    for (int k = 0; k < 6; k++) {
        const char *q = p;
        while (q < end && *q != ',') q++;
        if (k < 5 && q == end) return PARSE_ERR_FORMAT;
        if (k == 5 && q != end) return PARSE_ERR_FORMAT;
        if (!parse_int(p, q, &f[k])) return PARSE_ERR_FORMAT;
        p = q + 1;
    }

    if (f[0] < 0 || f[0] > 0xFFFFFFFFLL) return PARSE_ERR_RANGE;
    if (f[1] < 0 || f[1] > 65535) return PARSE_ERR_RANGE;
    for (int k = 2; k <= 4; k++) {
        if (f[k] < -32768 || f[k] > 32767) return PARSE_ERR_RANGE;
    }
    if (f[5] != 0 && f[5] != 1) return PARSE_ERR_RANGE;

    out->seq = (uint32_t)f[0];
    out->min_mm = (uint16_t)f[1];
    out->local_w = (int16_t)f[2];
    out->edge_v = (int16_t)f[3];
    out->edge_w = (int16_t)f[4];
    out->flag = (uint8_t)f[5];
    return PARSE_OK;
}

static size_t finish_line(char *buf, size_t cap, int n)
{
    if (n <= 0 || (size_t)n + 4 > cap) return 0; /* room for *XX\n */
    uint8_t cs = proto_checksum(buf, (size_t)n);
    int m = snprintf(buf + n, cap - (size_t)n, "*%02X\n", cs);
    if (m != 4) return 0;
    return (size_t)n + 4;
}

size_t proto_format_c(const c_line_t *c, char *buf, size_t cap)
{
    int n = snprintf(buf, cap, "C,%lu,%d,%d,%u,%u,%lu,%u,%u",
                     (unsigned long)c->seq, (int)c->v_out, (int)c->w_out,
                     (unsigned)c->mode, (unsigned)c->state,
                     (unsigned long)c->switch_us, (unsigned)c->n_sw,
                     (unsigned)c->bad_lines);
    if (n < 0 || (size_t)n >= cap) return 0;
    return finish_line(buf, cap, n);
}

size_t proto_format_v(const char *build_id, uint32_t sysclk_hz, char *buf, size_t cap)
{
    int n = snprintf(buf, cap, "V,%d,%s,%lu", PROTO_VERSION, build_id,
                     (unsigned long)sysclk_hz);
    if (n < 0 || (size_t)n >= cap) return 0;
    return finish_line(buf, cap, n);
}

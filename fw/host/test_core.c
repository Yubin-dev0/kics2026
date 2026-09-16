/* test_core.c - host unit tests for fw/core. Build and run: make -C fw/host test */
#include <stdio.h>
#include <string.h>

#include "app.h"
#include "proto.h"
#include "safety.h"

static int fails = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);     \
            fails++;                                                   \
        }                                                              \
    } while (0)

static uint32_t fake_cyc = 0;
static uint32_t cyc_now(void) { return fake_cyc; }

static size_t mk_s(char *buf, size_t cap, unsigned long seq, unsigned mm, int lv, int lw,
                   int ev, int ew, unsigned flag)
{
    int n = snprintf(buf, cap, "S,%lu,%u,%d,%d,%d,%d,%u", seq, mm, lv, lw, ev, ew, flag);
    n += snprintf(buf + n, cap - (size_t)n, "*%02X", proto_checksum(buf, (size_t)n));
    return (size_t)n;
}

static void test_checksum(void)
{
    CHECK(proto_checksum("V", 1) == 0x56);
}

static void test_parse(void)
{
    char b[128];
    s_line_t s;
    size_t n = mk_s(b, sizeof b, 4294967295UL, 65535, -7, -1820, 220, 1820, 1);
    CHECK(proto_parse_s(b, n, &s) == PARSE_OK);
    CHECK(s.seq == 4294967295UL && s.min_mm == 65535 && s.local_v == -7 && s.local_w == -1820);
    CHECK(s.edge_v == 220 && s.edge_w == 1820 && s.flag == 1);

    n = mk_s(b, sizeof b, 1, 300, 220, 0, 0, 0, 0);
    b[n - 1] = (b[n - 1] == '0') ? '1' : '0';
    CHECK(proto_parse_s(b, n, &s) == PARSE_ERR_CHECKSUM);

    n = mk_s(b, sizeof b, 1, 65536, 220, 0, 0, 0, 0);
    CHECK(proto_parse_s(b, n, &s) == PARSE_ERR_RANGE);
    n = mk_s(b, sizeof b, 1, 300, 220, 0, 0, 0, 2);
    CHECK(proto_parse_s(b, n, &s) == PARSE_ERR_RANGE);
    n = mk_s(b, sizeof b, 1, 300, 220, 40000, 0, 0, 0);
    CHECK(proto_parse_s(b, n, &s) == PARSE_ERR_RANGE);
    n = mk_s(b, sizeof b, 1, 300, 40000, 0, 0, 0, 0);
    CHECK(proto_parse_s(b, n, &s) == PARSE_ERR_RANGE);

    const char *bad[] = {"S,1,300,0,0,0,0*00",   "S,1,300,0,0,0,0,0,0*00", "S,,300,0,0,0,0,0*00",
                         "X,1,300,0,0,0,0,0*00", "S,1,300,0,0,0,0,0",      "S,1,3a0,0,0,0,0,0*00",
                         "S,1,300,0,0,0,0,-*00", "S,1,300,0,0,0,0,0*0"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        strcpy(b, bad[i]);
        char *star = strchr(b, '*');
        if (star && strlen(star) == 3) {
            /* recompute the checksum so only the format is wrong */
            sprintf(star, "*%02X", proto_checksum(b, (size_t)(star - b)));
        }
        if (proto_parse_s(b, strlen(b), &s) == PARSE_OK) {
            printf("FAIL accepted bad line: %s\n", b);
            fails++;
        }
    }
}

static void test_safety(void)
{
    struct {
        unsigned mm;
        int v;
        int st;
    } t[] = {
        {0, 0, ST_STOP},     {199, 0, ST_STOP},   {200, 100, ST_SLOW}, {290, 100, ST_SLOW},
        {291, 100, ST_SLOW}, {300, 110, ST_SLOW}, {399, 218, ST_SLOW}, {400, 220, ST_RUN},
        {65535, 220, ST_RUN},
    };
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        int16_t v;
        safety_state_t st = safety_eval((uint16_t)t[i].mm, &v);
        if (v != t[i].v || (int)st != t[i].st) {
            printf("FAIL safety mm=%u got v=%d st=%d\n", t[i].mm, v, (int)st);
            fails++;
        }
    }
    /* never faster than the float rule, never more than 1 mm/s slower */
    for (unsigned mm = 0; mm < 1000; mm++) {
        int16_t v;
        safety_eval((uint16_t)mm, &v);
        double r = mm / 1000.0, vf;
        if (r >= 0.40) {
            vf = 0.22;
        } else if (r >= 0.20) {
            vf = 0.22 * (r - 0.20) / 0.20;
            if (vf < 0.10) vf = 0.10;
        } else {
            vf = 0.0;
        }
        double d = vf * 1000.0 - v;
        if (!(d > -1e-6 && d < 1.0 + 1e-6)) {
            printf("FAIL float rule mm=%u v=%d vf=%.4f\n", mm, v, vf * 1000.0);
            fails++;
        }
    }
}

static void test_app(void)
{
    app_cfg_t cfg = {cyc_now, 180, "test", 180000000UL};
    app_t a;
    app_init(&a, &cfg);
    char in[128], out[128];
    size_t n;

    n = app_on_line(&a, "V*56", 4, 0, 0, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "V,2,test,180000000*", strlen("V,2,test,180000000*")) == 0);

    /* edge mode passes the edge command through; state still reported */
    n = mk_s(in, sizeof in, 1, 150, 220, 55, 200, -300, 0);
    n = app_on_line(&a, in, n, 0, 0, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,1,200,-300,0,2,0,0,0*", strlen("C,1,200,-300,0,2,0,0,0*")) == 0);

    /* switch to local: switch_us from cycle counts, safety v, local_w */
    fake_cyc = 180 * 37;
    n = mk_s(in, sizeof in, 2, 300, 220, 55, 200, -300, 1);
    n = app_on_line(&a, in, n, 0, 50, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,2,110,55,1,1,37,1,0*", strlen("C,2,110,55,1,1,37,1,0*")) == 0);

    /* same level again: no switch */
    n = mk_s(in, sizeof in, 3, 300, 220, 55, 200, -300, 1);
    n = app_on_line(&a, in, n, 0, 100, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,3,110,55,1,1,0,1,0*", strlen("C,3,110,55,1,1,0,1,0*")) == 0);

    /* local speed cap: turning in place (0), slower than the rule (80), faster (300),
     * negative (treated as 0); the reported state is still the safety state */
    n = mk_s(in, sizeof in, 3, 500, 0, 900, 200, -300, 1);
    n = app_on_line(&a, in, n, 0, 100, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,3,0,900,1,0,0,1,0*", strlen("C,3,0,900,1,0,0,1,0*")) == 0);
    n = mk_s(in, sizeof in, 3, 300, 80, 0, 200, -300, 1);
    n = app_on_line(&a, in, n, 0, 100, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,3,80,0,1,1,0,1,0*", strlen("C,3,80,0,1,1,0,1,0*")) == 0);
    n = mk_s(in, sizeof in, 3, 300, 300, 0, 200, -300, 1);
    n = app_on_line(&a, in, n, 0, 100, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,3,110,0,1,1,0,1,0*", strlen("C,3,110,0,1,1,0,1,0*")) == 0);
    n = mk_s(in, sizeof in, 3, 500, -50, 0, 200, -300, 1);
    n = app_on_line(&a, in, n, 0, 100, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,3,0,0,1,0,0,1,0*", strlen("C,3,0,0,1,0,0,1,0*")) == 0);

    /* bad line counted, no reply */
    n = app_on_line(&a, "S,4,300*00", 10, 0, 120, out, sizeof out);
    CHECK(n == 0 && a.bad_lines == 1);

    /* watchdog: fires once after 150 ms of silence */
    CHECK(app_poll(&a, 250, out, sizeof out) == 0);
    n = app_poll(&a, 251, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,3,0,0,1,3,0,1,1*", strlen("C,3,0,0,1,3,0,1,1*")) == 0);
    CHECK(app_poll(&a, 400, out, sizeof out) == 0);

    /* next valid line clears the watchdog */
    n = mk_s(in, sizeof in, 5, 500, 220, 0, 0, 0, 1);
    n = app_on_line(&a, in, n, 0, 500, out, sizeof out);
    CHECK(n > 0 && strncmp(out, "C,5,220,0,1,0,0,1,1*", strlen("C,5,220,0,1,0,0,1,1*")) == 0);
    CHECK(app_poll(&a, 700, out, sizeof out) > 0);
}

int main(void)
{
    test_checksum();
    test_parse();
    test_safety();
    test_app();
    if (fails) {
        printf("%d FAILED\n", fails);
        return 1;
    }
    printf("all tests passed\n");
    return 0;
}

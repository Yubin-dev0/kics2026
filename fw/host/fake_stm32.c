/* fake_stm32.c - fw/core behind a tty, so the bench and the N1 bridge can run without a
 * board. Usage: fake_stm32 <tty-path>   (see fw/README.md) */
#define _DEFAULT_SOURCE
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include "app.h"

static uint32_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000000ULL + (uint64_t)ts.tv_nsec / 1000U);
}

static uint32_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000ULL + (uint64_t)ts.tv_nsec / 1000000U);
}

static void put(int fd, const char *s, size_t n)
{
    while (n > 0) {
        ssize_t w = write(fd, s, n);
        if (w <= 0) return;
        s += w;
        n -= (size_t)w;
    }
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s <tty>\n", argv[0]);
        return 2;
    }
    int fd = open(argv[1], O_RDWR | O_NOCTTY);
    if (fd < 0) {
        perror("open");
        return 1;
    }
    struct termios t;
    if (tcgetattr(fd, &t) == 0) {
        cfmakeraw(&t);
        tcsetattr(fd, TCSANOW, &t);
    }

    /* host "cycle counter" ticks once per microsecond */
    app_cfg_t cfg = {now_us, 1, "host-fake", 0};
    app_t app;
    app_init(&app, &cfg);

    char line[PROTO_LINE_MAX + 1], out[128];
    size_t len = 0;
    int overlong = 0;
    for (;;) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, 5) > 0) {
            char buf[256];
            ssize_t n = read(fd, buf, sizeof buf);
            if (n <= 0) return 0;
            uint32_t t_rx = now_us();
            for (ssize_t i = 0; i < n; i++) {
                char c = buf[i];
                if (c == '\r') continue;
                if (c == '\n') {
                    if (overlong) {
                        app_count_bad(&app);
                    } else {
                        size_t m = app_on_line(&app, line, len, t_rx, now_ms(), out, sizeof out);
                        put(fd, out, m);
                    }
                    len = 0;
                    overlong = 0;
                } else if (len < PROTO_LINE_MAX) {
                    line[len++] = c;
                } else {
                    overlong = 1;
                }
            }
        }
        put(fd, out, app_poll(&app, now_ms(), out, sizeof out));
    }
}

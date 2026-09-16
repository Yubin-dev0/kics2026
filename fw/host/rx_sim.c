/* rx_sim.c - drives fw/stm32/port/app_port.c the way the F4 HAL does with circular DMA and
 * HAL_UARTEx_ReceiveToIdle_DMA, and checks that every S line is answered exactly once.
 *
 * HAL behaviour reproduced (stm32f4xx_hal_uart.c, circular mode):
 *   - DMA transfer complete at the wrap:         callback(Size = 256)
 *   - IDLE with 0 < NDTR < 256:                  callback(Size = 256 - NDTR)
 *   - IDLE with NDTR == 256 (burst ended at wrap): callback(Size = 256)
 * Lines are optionally split into two USB chunks (an extra IDLE mid-line), as the ST-LINK
 * virtual COM port does. The pre-fix handler trusted Size and re-fed the whole buffer on the
 * third case: A2 run_2 showed 146 bad lines with every real line answered. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app_port.h"
#include "proto.h"

static CoreDebug_Type cd;
static DWT_Type dwt;
CoreDebug_Type *CoreDebug = &cd;
DWT_Type *DWT = &dwt;
uint32_t SystemCoreClock = 180000000UL;

static DMA_Stream_TypeDef stream;
static DMA_HandleTypeDef hdma = {&stream};
static UART_HandleTypeDef huart = {&hdma};
static uint8_t *dma_buf;
static uint32_t tick;
static unsigned long expect_seq;
static int replies, stale;
static unsigned last_bad;

int HAL_UARTEx_ReceiveToIdle_DMA(UART_HandleTypeDef *h, uint8_t *buf, uint16_t size)
{
    (void)h;
    dma_buf = buf;
    stream.NDTR = size;
    return 0;
}

int HAL_UART_Transmit(UART_HandleTypeDef *h, uint8_t *s, uint16_t n, uint32_t timeout)
{
    (void)h;
    (void)timeout;
    char tmp[128];
    memcpy(tmp, s, n);
    tmp[n] = 0;
    unsigned long seq;
    unsigned v, w, m, st, us, nsw, bad;
    if (sscanf(tmp, "C,%lu,%u,%u,%u,%u,%u,%u,%u", &seq, &v, &w, &m, &st, &us, &nsw, &bad) == 8) {
        if (seq == expect_seq) replies++;
        else stale++;
        last_bad = bad;
    }
    return 0;
}

uint32_t HAL_GetTick(void) { return tick; }

static void rx_byte(uint8_t c)
{
    dma_buf[256 - stream.NDTR] = c;
    if (--stream.NDTR == 0) {
        stream.NDTR = 256;
        HAL_UARTEx_RxEventCallback(&huart, 256); /* transfer complete */
    }
}

static void idle(void)
{
    if (stream.NDTR > 0 && stream.NDTR < 256)
        HAL_UARTEx_RxEventCallback(&huart, (uint16_t)(256 - stream.NDTR));
    else if (stream.NDTR == 256)
        HAL_UARTEx_RxEventCallback(&huart, 256);
}

static int run(unsigned seed, int split_every, int lines)
{
    srand(seed);
    replies = stale = 0;
    app_port_init(&huart);
    unsigned bad0 = 0;
    for (int k = 1; k <= lines; k++) {
        char body[64], line[80];
        int n = snprintf(body, sizeof body, "S,%d,%d,220,%d,150,%d,0", k, 150 + rand() % 650,
                         rand() % 3641 - 1820, rand() % 3641 - 1820);
        int m = snprintf(line, sizeof line, "%s*%02X\n", body, proto_checksum(body, (size_t)n));
        int split = (split_every && rand() % split_every == 0) ? 1 + rand() % (m - 1) : m;
        expect_seq = (unsigned long)k;
        for (int i = 0; i < m; i++) {
            rx_byte((uint8_t)line[i]);
            if (i + 1 == split && split < m) idle();
        }
        idle();
        tick += 50;
        app_port_loop();
        if (k == 1) bad0 = last_bad;
    }
    int ok = replies == lines && stale == 0 && last_bad == bad0;
    printf("seed %u split 1/%d: replies %d/%d stale %d bad_lines +%u %s\n", seed, split_every,
           replies, lines, stale, last_bad - bad0, ok ? "ok" : "FAIL");
    return ok;
}

int main(void)
{
    int ok = 1;
    for (unsigned seed = 1; seed <= 3; seed++) {
        ok &= run(seed, 4, 2000); /* one line in four arrives in two chunks */
        ok &= run(seed, 1, 2000); /* every line arrives in two chunks */
    }
    printf(ok ? "rx sim passed\n" : "rx sim FAILED\n");
    return ok ? 0 : 1;
}

/* app_port.c - binds fw/core to the NUCLEO-F446RE HAL (bare metal, no RTOS).
 *
 * RX: USART2 + DMA1 Stream5 in circular mode, HAL_UARTEx_ReceiveToIdle_DMA.
 *     Bytes are assembled into lines in the RX event callback (interrupt context);
 *     the DWT cycle count at callback entry is stored with each completed line.
 * Main loop: takes lines off a small queue, runs app_on_line, sends the reply
 *     (blocking TX, ~0.5 ms per line at 921600), and polls the watchdog.
 *
 * Compiled through n2_safety/Core/Src/fw_sources.c, which #includes this file and fw/core
 * by relative path. No linked folders or include paths are needed, so regenerating code
 * with STM32CubeMX cannot break the build. Wiring: see fw/stm32/README.md.
 */
#include "app_port.h"

#include <string.h>

#include "../../core/app.h" /* relative: the project needs no extra include path */

#define RX_DMA_SIZE 256
#define LINE_QUEUE_LEN 4

typedef struct {
    char buf[PROTO_LINE_MAX];
    uint8_t len;
    uint32_t t_rx_cyc;
} queued_line_t;

static UART_HandleTypeDef *port_huart;
static app_t app;

static uint8_t rx_dma[RX_DMA_SIZE];
static uint16_t rx_old_pos;

/* line assembly state, touched only in the RX callback */
static char asm_buf[PROTO_LINE_MAX];
static uint8_t asm_len;
static uint8_t asm_overlong;

/* single-producer (ISR) / single-consumer (main) ring */
static queued_line_t queue[LINE_QUEUE_LEN];
static volatile uint8_t q_head; /* written by ISR */
static volatile uint8_t q_tail; /* written by main */
static volatile uint16_t isr_drops; /* overlong lines, queue overflow, UART errors */

#ifdef DEBUG
#define BUILD_CFG "dbg"
#else
#define BUILD_CFG "rel"
#endif

static uint32_t cyc_now(void) { return DWT->CYCCNT; }

static void dwt_enable(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static void rx_start(void)
{
    rx_old_pos = 0;
    HAL_UARTEx_ReceiveToIdle_DMA(port_huart, rx_dma, RX_DMA_SIZE);
    /* only idle and buffer-wrap events are needed */
    __HAL_DMA_DISABLE_IT(port_huart->hdmarx, DMA_IT_HT);
}

static void feed(const uint8_t *p, uint16_t n, uint32_t t)
{
    for (uint16_t i = 0; i < n; i++) {
        char c = (char)p[i];
        if (c == '\r') continue;
        if (c != '\n') {
            if (asm_len < PROTO_LINE_MAX) {
                asm_buf[asm_len++] = c;
            } else {
                asm_overlong = 1;
            }
            continue;
        }
        uint8_t next = (uint8_t)((q_head + 1) % LINE_QUEUE_LEN);
        if (asm_overlong || next == q_tail) {
            isr_drops++;
        } else {
            memcpy(queue[q_head].buf, asm_buf, asm_len);
            queue[q_head].len = asm_len;
            queue[q_head].t_rx_cyc = t;
            q_head = next;
        }
        asm_len = 0;
        asm_overlong = 0;
    }
}

/* The receive position is read from the DMA counter, not from the Size argument.
 * With circular DMA the F4 HAL calls this for the TC event with Size = 256 and, when a burst
 * ends exactly at the wrap, once more for the IDLE event with Size = 256 again. Treating that
 * second call as "256 new bytes" re-fed the whole buffer: stale lines were processed a second
 * time and fragments counted as bad lines (A2 run_2: 146 bad lines, every real line answered).
 * Working from the counter makes the handler idempotent: a call with no new bytes does nothing.
 * TC and IDLE run at the same NVIC priority, so this handler never preempts itself.
 * Limit: more than RX_DMA_SIZE bytes between two calls would be missed; at 20 Hz with
 * ~30 byte lines the buffer holds 8 lines, and every line ends in an IDLE event. */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t size)
{
    (void)size;
    if (huart != port_huart) return;
    uint32_t t = DWT->CYCCNT; /* reference point t0 for switch_us */
    uint16_t pos = (uint16_t)(RX_DMA_SIZE - __HAL_DMA_GET_COUNTER(huart->hdmarx));
    if (pos >= RX_DMA_SIZE) pos = 0; /* counter reads 0 only at the instant of reload */
    if (pos == rx_old_pos) return;
    if (pos > rx_old_pos) {
        feed(&rx_dma[rx_old_pos], (uint16_t)(pos - rx_old_pos), t);
    } else {
        feed(&rx_dma[rx_old_pos], (uint16_t)(RX_DMA_SIZE - rx_old_pos), t);
        feed(rx_dma, pos, t);
    }
    rx_old_pos = pos;
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart != port_huart) return;
    isr_drops++;
    asm_len = 0;
    asm_overlong = 1; /* discard the partial line up to the next '\n' */
    rx_start();
}

void app_port_init(UART_HandleTypeDef *huart)
{
    port_huart = huart;
    dwt_enable();
    app_cfg_t cfg = {
        .cyc_now = cyc_now,
        .cyc_per_us = SystemCoreClock / 1000000U,
        .build_id = __DATE__ " " __TIME__ " " BUILD_CFG,
        .sysclk_hz = SystemCoreClock,
    };
    app_init(&app, &cfg);
    rx_start();
}

static void send(const char *s, size_t n)
{
    if (n) HAL_UART_Transmit(port_huart, (uint8_t *)s, (uint16_t)n, 10);
}

void app_port_loop(void)
{
    char out[128];
    static uint16_t drops_seen;

    uint16_t drops = isr_drops;
    while (drops_seen != drops) {
        app_count_bad(&app);
        drops_seen++;
    }

    while (q_tail != q_head) {
        queued_line_t *l = &queue[q_tail];
        send(out, app_on_line(&app, l->buf, l->len, l->t_rx_cyc, HAL_GetTick(), out,
                              sizeof out));
        q_tail = (uint8_t)((q_tail + 1) % LINE_QUEUE_LEN);
    }

    send(out, app_poll(&app, HAL_GetTick(), out, sizeof out));
}

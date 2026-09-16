/* app_port.h - NUCLEO-F446RE binding for fw/core. See fw/stm32/README.md. */
#ifndef APP_PORT_H
#define APP_PORT_H

#include "main.h" /* CubeMX-generated: pulls in stm32f4xx_hal.h */

void app_port_init(UART_HandleTypeDef *huart); /* after MX_USART2_UART_Init() */
void app_port_loop(void);                      /* every pass of while (1) */

#endif

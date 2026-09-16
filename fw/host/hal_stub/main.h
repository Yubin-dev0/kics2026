/* hal_stub/main.h - just enough of the F4 HAL to compile fw/stm32/port/app_port.c on the host.
 * Shapes follow the real HAL (hdmarx->Instance->NDTR) so the port code is compiled unchanged. */
#ifndef HAL_STUB_MAIN_H
#define HAL_STUB_MAIN_H

#include <stdint.h>

typedef struct { uint32_t CR, NDTR; } DMA_Stream_TypeDef;
typedef struct { DMA_Stream_TypeDef *Instance; } DMA_HandleTypeDef;
typedef struct { DMA_HandleTypeDef *hdmarx; } UART_HandleTypeDef;
typedef struct { uint32_t DEMCR; } CoreDebug_Type;
typedef struct { uint32_t CTRL, CYCCNT; } DWT_Type;

extern CoreDebug_Type *CoreDebug;
extern DWT_Type *DWT;
extern uint32_t SystemCoreClock;

#define CoreDebug_DEMCR_TRCENA_Msk (1u << 24)
#define DWT_CTRL_CYCCNTENA_Msk 1u
#define DMA_IT_HT 4u
#define __HAL_DMA_DISABLE_IT(__HANDLE__, __INTERRUPT__) ((__HANDLE__)->Instance->CR &= ~(__INTERRUPT__))
#define __HAL_DMA_GET_COUNTER(__HANDLE__) ((__HANDLE__)->Instance->NDTR)

int HAL_UARTEx_ReceiveToIdle_DMA(UART_HandleTypeDef *huart, uint8_t *buf, uint16_t size);
int HAL_UART_Transmit(UART_HandleTypeDef *huart, uint8_t *buf, uint16_t size, uint32_t timeout);
uint32_t HAL_GetTick(void);
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size);
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart);

#endif

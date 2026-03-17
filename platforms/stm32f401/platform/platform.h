/**
 * \file   platform.h
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform header file
 * \note   This file defines the platform-specific functions and includes necessary headers for the STM32F4 platform.
 */

#ifndef PLATFORM_H_
#define PLATFORM_H_

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

// Platform config
#include "platform_config.h"

// STM32F4 HAL includes
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_uart.h"

// System
#include <stdint.h>

/**********************************************************************************************************************
 * Public Function Prototypes
 **********************************************************************************************************************/

void SysTick_Handler(void);

#if (CLI_MODE == CLI_MODE_UART)
void HAL_UART_MspInit(UART_HandleTypeDef* huart);
void HAL_UART_MspDeInit(UART_HandleTypeDef* huart);
void USART1_IRQHandler(void);
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart);
#endif

#endif // PLATFORM_H__

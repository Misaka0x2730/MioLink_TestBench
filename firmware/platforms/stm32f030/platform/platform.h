/**
 * \file   platform.h
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform header file
 * \note   This file defines the platform-specific functions and includes necessary headers for the STM32F0 platform.
 */

#ifndef PLATFORM_H_
#define PLATFORM_H_

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

// Platform config
#include "platform_config.h"

// STM32F0 HAL includes
#include "stm32f0xx_hal.h"
#include "stm32f0xx_hal_uart.h"

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

#endif // PLATFORM_H_

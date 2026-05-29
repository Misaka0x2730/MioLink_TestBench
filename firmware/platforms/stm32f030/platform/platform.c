/**
 * \file   platform.c
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform source file
 * \note   This file implements the platform-specific functions for the STM32F0 platform,
 *         including initialization, unique ID retrieval, and tick count retrieval.
 */

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

// Current platform includes
#include "platform.h"

// Platform common includes
#include "platform_iface.h"
#include "platform_config.h"

// STM32F0 HAL includes
#include "stm32f0xx_hal.h"
#include "stm32f0xx_hal_rcc.h"
#include "stm32f0xx_hal_uart.h"

// System
#include <stdio.h>

/**********************************************************************************************************************
 * Private Definitions
 **********************************************************************************************************************/

#define ID_STRING_SIZE 25 /* 96-bit UID as 24 hex chars + null */

/**********************************************************************************************************************
 * Private Function Prototypes
 **********************************************************************************************************************/

static void Error_Handler(void);
static void SystemClock_Config(void);

/**********************************************************************************************************************
 * Private Data
 **********************************************************************************************************************/

#if (CLI_MODE == CLI_MODE_UART)
static UART_HandleTypeDef huart1;
static uint8_t uart_rx_data = 0;
#endif

/**********************************************************************************************************************
 * Private Functions
 **********************************************************************************************************************/

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    /* HSI 8 MHz / 2 = 4 MHz into PLL, x12 -> 48 MHz SYSCLK (max for STM32F030). */
    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
    RCC_OscInitStruct.HSIState = RCC_HSI_ON;
    RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
    RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL12;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
    {
        Error_Handler();
    }

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                  RCC_CLOCKTYPE_PCLK1;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_1) != HAL_OK)
    {
        Error_Handler();
    }
}

static void Error_Handler(void)
{
    __disable_irq();
    while (1)
    {
    }
}

/**********************************************************************************************************************
 * Public Functions
 **********************************************************************************************************************/

void SysTick_Handler(void)
{
    HAL_IncTick();
}

#if (CLI_MODE == CLI_MODE_UART)
void HAL_UART_MspInit(UART_HandleTypeDef* huart)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    if (huart->Instance == USART1)
    {
        __HAL_RCC_USART1_CLK_ENABLE();

        __HAL_RCC_GPIOA_CLK_ENABLE();

        /* PA9 = USART1_TX, PA10 = USART1_RX, both on AF1 for STM32F030. */
        GPIO_InitStruct.Pin = GPIO_PIN_9 | GPIO_PIN_10;
        GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
        GPIO_InitStruct.Pull = GPIO_NOPULL;
        GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
        GPIO_InitStruct.Alternate = GPIO_AF1_USART1;
        HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

        HAL_NVIC_SetPriority(USART1_IRQn, 0, 0);
        HAL_NVIC_EnableIRQ(USART1_IRQn);
    }
}

void HAL_UART_MspDeInit(UART_HandleTypeDef* huart)
{
    if (huart->Instance == USART1)
    {
        __HAL_RCC_USART1_CLK_DISABLE();

        HAL_GPIO_DeInit(GPIOA, GPIO_PIN_9 | GPIO_PIN_10);

        HAL_NVIC_DisableIRQ(USART1_IRQn);
    }
}

void USART1_IRQHandler(void)
{
    HAL_UART_IRQHandler(&huart1);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    cli_rx_callback(uart_rx_data);

    HAL_UART_Receive_IT(&huart1, &uart_rx_data, 1);
}

void platform_cli_uart_init(const uint32_t baudrate)
{
    huart1.Instance = USART1;
    huart1.Init.BaudRate = baudrate;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    huart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart1) != HAL_OK)
    {
        Error_Handler();
    }

    HAL_UART_Receive_IT(&huart1, &uart_rx_data, 1);
}

void platform_cli_uart_transmit(const uint8_t *data, uint16_t size)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)data, size, 0xFFFF);
}
#endif

#if (CONFIG_TEST_SWO_ENABLED == 1)
#error "SWO trace is not available on STM32F030 (Cortex-M0 has no ITM/TPIU). Disable CONFIG_TEST_SWO."
#endif

void platform_init(void)
{
    SystemClock_Config();
    SystemCoreClockUpdate();

    HAL_Init();
}

char *platform_get_id(void)
{
    /* 96-bit UID as 24 hex chars + null */
    static char id_string[ID_STRING_SIZE] = {0};
    const uint32_t *uid = (const uint32_t *)UID_BASE;

    if (id_string[0] != '\0')
    {
        return id_string;
    }

    snprintf(id_string, sizeof(id_string), "%08lX%08lX%08lX", uid[0], uid[1], uid[2]);

    return id_string;
}

uint32_t platform_get_tick(void)
{
    return HAL_GetTick();
}

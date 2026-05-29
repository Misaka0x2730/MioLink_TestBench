/**
 * \file   platform.c
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform source file
 * \note   This file implements the platform-specific functions for the STM32F4 platform,
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

// STM32F4 HAL includes
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_rcc.h"
#include "stm32f4xx_hal_uart.h"

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
UART_HandleTypeDef huart1;
static uint8_t uart_rx_data = 0;
#endif

/**********************************************************************************************************************
 * Private Functions
 **********************************************************************************************************************/

static void SystemClock_Config(void)
{   
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    __HAL_RCC_SYSCFG_CLK_ENABLE();
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    RCC_OscInitStruct.HSEState = RCC_HSE_ON;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    RCC_OscInitStruct.PLL.PLLM = 25;
    RCC_OscInitStruct.PLL.PLLN = 168;
    RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
    RCC_OscInitStruct.PLL.PLLQ = 4;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
    {
        Error_Handler();
    }

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                  RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
    RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
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

        GPIO_InitStruct.Pin = GPIO_PIN_9;
        GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
        GPIO_InitStruct.Pull = GPIO_NOPULL;
        GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
        GPIO_InitStruct.Alternate = GPIO_AF7_USART1;
        HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

        GPIO_InitStruct.Pin = GPIO_PIN_10;
        GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
        GPIO_InitStruct.Pull = GPIO_NOPULL;
        GPIO_InitStruct.Alternate = GPIO_AF7_USART1;
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
void platform_swo_init(const uint32_t baudrate)
{
    /* Route the TRACE pin (PB3) to the TPIU and select asynchronous mode. */
    MODIFY_REG(DBGMCU->CR, DBGMCU_CR_TRACE_MODE, 0U);
    SET_BIT(DBGMCU->CR, DBGMCU_CR_TRACE_IOEN);

    /* Enable the Cortex-M debug trace subsystem. */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;

    /* TPIU: Asynchronous NRZ (UART-like) encoding. */
    TPIU->SPPR = 2U;
    TPIU->ACPR = (SystemCoreClock / baudrate) - 1U;

    /* Disable the TPIU continuous formatter so the SWO line carries a raw ITM stream.
     * With EnFCont = 1 (reset default) the formatter wraps data into 16-byte frames
     * with bit-0 stuffing and an auxiliary byte, which an async-NRZ SWO consumer
     * (MioLink) does not deframe. */
    TPIU->FFCR = 0U;

    /* Unlock the ITM (lock access register magic). */
    ITM->LAR = 0xC5ACCE55U;

    /* Enable ITM with bus ID 1, enable forwarding to the SWO line. */
    ITM->TCR = ((1U << ITM_TCR_TRACEBUSID_Pos) & ITM_TCR_TRACEBUSID_Msk) | ITM_TCR_SWOENA_Msk | ITM_TCR_ITMENA_Msk;

    /* Allow unprivileged code to use every stimulus port. */
    ITM->TPR = 0xFFFFFFFFU;

    /* Enable stimulus port 0, which is the channel the test uses. */
    ITM->TER = 0x1U;
}

void platform_swo_send(const uint8_t *data, uint16_t size)
{
    for (uint16_t i = 0U; i < size; i++)
    {
        ITM_SendChar((uint32_t)data[i]);
    }
}
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

/**
 * \file   platform.c
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform source file
 * \note   This file implements the platform-specific functions for the HC32F460 platform,
 *         including initialization, unique ID retrieval, and tick count retrieval.
 */

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

#include "platform.h"

#include "platform_iface.h"
#include "platform_config.h"

#include <stdio.h>

/**********************************************************************************************************************
 * Private Definitions
 **********************************************************************************************************************/

#define ID_STRING_SIZE 25 /* 96-bit UID as 24 hex chars + null */

#define LL_PERIPH_SEL  (LL_PERIPH_GPIO | LL_PERIPH_FCG | LL_PERIPH_PWC_CLK_RMU | \
                        LL_PERIPH_EFM | LL_PERIPH_SRAM)

/* External 8 MHz crystal connected to PH0 (XTAL_IN) / PH1 (XTAL_OUT). */
#define XTAL_GPIO_PORT  (GPIO_PORT_H)
#define XTAL_GPIO_PIN   (GPIO_PIN_00 | GPIO_PIN_01)

#if (CLI_MODE == CLI_MODE_UART)
/* PA9 = USART1_TX (GPIO_FUNC_32), PA10 = USART1_RX (GPIO_FUNC_33). */
#define UART_CLI_UNIT            (CM_USART1)
#define UART_CLI_FCG_PERIPH      (FCG1_PERIPH_USART1)

#define UART_CLI_TX_PORT         (GPIO_PORT_A)
#define UART_CLI_TX_PIN          (GPIO_PIN_09)
#define UART_CLI_TX_FUNC         (GPIO_FUNC_32)

#define UART_CLI_RX_PORT         (GPIO_PORT_A)
#define UART_CLI_RX_PIN          (GPIO_PIN_10)
#define UART_CLI_RX_FUNC         (GPIO_FUNC_33)

#define UART_CLI_RX_IRQn         (INT000_IRQn)
#define UART_CLI_RX_INT_SRC      (INT_SRC_USART1_RI)

#define UART_CLI_RX_ERR_IRQn     (INT001_IRQn)
#define UART_CLI_RX_ERR_INT_SRC  (INT_SRC_USART1_EI)
#endif

/**********************************************************************************************************************
 * Private Function Prototypes
 **********************************************************************************************************************/

static void Error_Handler(void);
static void SystemClock_Config(void);

#if (CLI_MODE == CLI_MODE_UART)
static void USART_CLI_RxFull_IrqCallback(void);
static void USART_CLI_RxError_IrqCallback(void);
#endif

/**********************************************************************************************************************
 * Private Data
 **********************************************************************************************************************/

static volatile uint32_t g_tick_ms = 0;

/**********************************************************************************************************************
 * Private Functions
 **********************************************************************************************************************/

static void SystemClock_Config(void)
{
    stc_clock_xtal_init_t stcXtalInit;
    stc_clock_pll_init_t  stcMpllInit;

    /* XTAL 8 MHz -> MPLL: VCO = (8 / PLLM) * PLLN = (8 / 1) * 50 = 400 MHz;
     * SYSCLK = VCO / PLLP = 400 / 2 = 200 MHz. */
    (void)CLK_XtalStructInit(&stcXtalInit);
    (void)CLK_PLLStructInit(&stcMpllInit);

    /* Bus dividers chosen so all PCLK/HCLK domains stay within HC32F460 limits at SYSCLK=200MHz. */
    CLK_SetClockDiv(CLK_BUS_CLK_ALL, (CLK_HCLK_DIV1  | CLK_EXCLK_DIV2  | CLK_PCLK0_DIV1 |
                                      CLK_PCLK1_DIV2 | CLK_PCLK2_DIV4  | CLK_PCLK3_DIV4 |
                                      CLK_PCLK4_DIV2));

    /* Configure XTAL pins to analog mode and enable the oscillator. */
    GPIO_AnalogCmd(XTAL_GPIO_PORT, XTAL_GPIO_PIN, ENABLE);

    stcXtalInit.u8Mode       = CLK_XTAL_MD_OSC;
    stcXtalInit.u8Drv        = CLK_XTAL_DRV_ULOW; /* 8 MHz crystal falls in the 4-8 MHz range. */
    stcXtalInit.u8State      = CLK_XTAL_ON;
    stcXtalInit.u8StableTime = CLK_XTAL_STB_2MS;
    if (LL_OK != CLK_XtalInit(&stcXtalInit)) {
        Error_Handler();
    }

    stcMpllInit.PLLCFGR = 0UL;
    stcMpllInit.PLLCFGR_f.PLLM   = 1UL  - 1UL;
    stcMpllInit.PLLCFGR_f.PLLN   = 50UL - 1UL;
    stcMpllInit.PLLCFGR_f.PLLP   = 2UL  - 1UL;
    stcMpllInit.PLLCFGR_f.PLLQ   = 2UL  - 1UL;
    stcMpllInit.PLLCFGR_f.PLLR   = 2UL  - 1UL;
    stcMpllInit.PLLCFGR_f.PLLSRC = CLK_PLL_SRC_XTAL;
    stcMpllInit.u8PLLState       = CLK_PLL_ON;

    if (LL_OK != CLK_PLLInit(&stcMpllInit)) {
        Error_Handler();
    }

    while (SET != CLK_GetStableStatus(CLK_STB_FLAG_PLL)) {
        ;
    }

    /* SRAM wait cycles for high SYSCLK. */
    SRAM_SetWaitCycle(SRAM_SRAMH, SRAM_WAIT_CYCLE0, SRAM_WAIT_CYCLE0);
    SRAM_SetWaitCycle((SRAM_SRAM12 | SRAM_SRAM3 | SRAM_SRAMR), SRAM_WAIT_CYCLE1, SRAM_WAIT_CYCLE1);

    /* Flash wait cycles must be raised before switching to a high SYSCLK. */
    (void)EFM_SetWaitCycle(EFM_WAIT_CYCLE5);

    /* GPIO read wait: 3 cycles covers the 126-200 MHz range. */
    GPIO_SetReadWaitCycle(GPIO_RD_WAIT3);

    /* Switch regulator driver ability before bumping SYSCLK above 84 MHz. */
    (void)PWC_HighSpeedToHighPerformance();

    CLK_SetSysClockSrc(CLK_SYSCLK_SRC_PLL);
}

static void Error_Handler(void)
{
    __disable_irq();
    while (1) {
    }
}

#if (CLI_MODE == CLI_MODE_UART)
static void USART_CLI_RxFull_IrqCallback(void)
{
    uint8_t data = (uint8_t)USART_ReadData(UART_CLI_UNIT);
    cli_rx_callback(data);
}

static void USART_CLI_RxError_IrqCallback(void)
{
    (void)USART_ReadData(UART_CLI_UNIT);
    USART_ClearStatus(UART_CLI_UNIT,
                      (USART_FLAG_PARITY_ERR | USART_FLAG_FRAME_ERR | USART_FLAG_OVERRUN));
}

static void install_irq_handler(const stc_irq_signin_config_t *cfg, uint32_t prio)
{
    (void)INTC_IrqSignIn(cfg);
    NVIC_ClearPendingIRQ(cfg->enIRQn);
    NVIC_SetPriority(cfg->enIRQn, prio);
    NVIC_EnableIRQ(cfg->enIRQn);
}
#endif

/**********************************************************************************************************************
 * Public Functions
 **********************************************************************************************************************/

void SysTick_Handler(void)
{
    g_tick_ms++;
}

#if (CLI_MODE == CLI_MODE_UART)
void platform_cli_uart_init(const uint32_t baudrate)
{
    stc_usart_uart_init_t stcUartInit;
    stc_irq_signin_config_t stcIrqSigninConfig;

    LL_PERIPH_WE(LL_PERIPH_SEL);

    GPIO_SetFunc(UART_CLI_TX_PORT, UART_CLI_TX_PIN, UART_CLI_TX_FUNC);
    GPIO_SetFunc(UART_CLI_RX_PORT, UART_CLI_RX_PIN, UART_CLI_RX_FUNC);

    FCG_Fcg1PeriphClockCmd(UART_CLI_FCG_PERIPH, ENABLE);

    (void)USART_UART_StructInit(&stcUartInit);
    stcUartInit.u32ClockDiv      = USART_CLK_DIV1;
    stcUartInit.u32Baudrate      = baudrate;
    stcUartInit.u32OverSampleBit = USART_OVER_SAMPLE_8BIT;

    if (LL_OK != USART_UART_Init(UART_CLI_UNIT, &stcUartInit, NULL)) {
        Error_Handler();
    }

    stcIrqSigninConfig.enIRQn      = UART_CLI_RX_IRQn;
    stcIrqSigninConfig.enIntSrc    = UART_CLI_RX_INT_SRC;
    stcIrqSigninConfig.pfnCallback = &USART_CLI_RxFull_IrqCallback;
    install_irq_handler(&stcIrqSigninConfig, DDL_IRQ_PRIO_DEFAULT);

    stcIrqSigninConfig.enIRQn      = UART_CLI_RX_ERR_IRQn;
    stcIrqSigninConfig.enIntSrc    = UART_CLI_RX_ERR_INT_SRC;
    stcIrqSigninConfig.pfnCallback = &USART_CLI_RxError_IrqCallback;
    install_irq_handler(&stcIrqSigninConfig, DDL_IRQ_PRIO_DEFAULT);

    LL_PERIPH_WP(LL_PERIPH_SEL);

    USART_FuncCmd(UART_CLI_UNIT, (USART_TX | USART_RX | USART_INT_RX), ENABLE);
}

void platform_cli_uart_transmit(const uint8_t *data, uint16_t size)
{
    (void)USART_UART_Trans(UART_CLI_UNIT, data, size, USART_MAX_TIMEOUT);
}
#endif

#if (CONFIG_TEST_SWO_ENABLED == 1)
void platform_swo_init(const uint32_t baudrate)
{
    /* Enable the Cortex-M debug trace subsystem. */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;

    /* TPIU: Asynchronous NRZ (UART-like) encoding. */
    TPI->SPPR = 2U;
    TPI->ACPR = (SystemCoreClock / baudrate) - 1U;

    /* Disable the TPIU continuous formatter so the SWO line carries a raw ITM stream. */
    TPI->FFCR = 0U;

    /* Unlock the ITM. */
    ITM->LAR = 0xC5ACCE55U;

    ITM->TCR = ((1U << ITM_TCR_TraceBusID_Pos) & ITM_TCR_TraceBusID_Msk) |
               ITM_TCR_SWOENA_Msk | ITM_TCR_ITMENA_Msk;
    ITM->TPR = 0xFFFFFFFFU;
    ITM->TER = 0x1U;
}

void platform_swo_send(const uint8_t *data, uint16_t size)
{
    for (uint16_t i = 0U; i < size; i++) {
        ITM_SendChar((uint32_t)data[i]);
    }
}
#endif

void platform_init(void)
{
    LL_PERIPH_WE(LL_PERIPH_SEL);

    SystemClock_Config();
    SystemCoreClockUpdate();

    if (SysTick_Config(SystemCoreClock / 1000U) != 0U) {
        Error_Handler();
    }

    LL_PERIPH_WP(LL_PERIPH_SEL);
}

char *platform_get_id(void)
{
    /* 96-bit UID as 24 hex chars + null */
    static char id_string[ID_STRING_SIZE] = {0};

    if (id_string[0] != '\0') {
        return id_string;
    }

    const uint32_t uid0 = CM_EFM->UQID0;
    const uint32_t uid1 = CM_EFM->UQID1;
    const uint32_t uid2 = CM_EFM->UQID2;

    snprintf(id_string, sizeof(id_string), "%08lX%08lX%08lX",
             (unsigned long)uid0, (unsigned long)uid1, (unsigned long)uid2);

    return id_string;
}

uint32_t platform_get_tick(void)
{
    return g_tick_ms;
}

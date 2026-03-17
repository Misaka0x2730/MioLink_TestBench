/**
 * \file   platform_config.h
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform configuration header file
 * \note   This file defines the configuration options for platform-specific implementations.
 */

#ifndef PLATFORM_CONFIG_H_
#define PLATFORM_CONFIG_H_

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

// System
#include <stdint.h>

/**********************************************************************************************************************
 * Public Definitions
 **********************************************************************************************************************/

#define CLI_MODE_RTT 1
#define CLI_MODE_UART 2

#if defined(CONFIG_CLI_USE_RTT) && defined(CONFIG_CLI_USE_UART)
#error "Multiple CLI modes defined. Please define only one of CONFIG_CLI_USE_RTT or CONFIG_CLI_USE_UART."
#endif

#if !defined(CONFIG_CLI_USE_RTT) && !defined(CONFIG_CLI_USE_UART)
#error "No CLI mode defined. Please define either CONFIG_CLI_USE_RTT or CONFIG_CLI_USE_UART."
#endif

#if defined(CONFIG_CLI_USE_RTT)
#define CLI_MODE CLI_MODE_RTT
#elif defined(CONFIG_CLI_USE_UART)
#define CLI_MODE CLI_MODE_UART
#endif

#if (CLI_MODE == CLI_MODE_RTT) && (!defined(CONFIG_TEST_RTT_CHANNEL))
#define CONFIG_TEST_RTT_CHANNEL 0
#endif

#if (CLI_MODE == CLI_MODE_UART) && (!defined(CONFIG_TEST_UART_BAUDRATE))
#define CONFIG_TEST_UART_BAUDRATE 115200
#endif


#endif // PLATFORM_CONFIG_H_
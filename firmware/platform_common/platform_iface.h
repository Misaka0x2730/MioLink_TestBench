/**
 * \file   platform_iface.h
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform interface header file
 * \note   This file defines the interface for platform-specific implementations.
 */

#ifndef PLATFORM_IFACE_H_
#define PLATFORM_IFACE_H_

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

// Platform configuration
#include "platform_config.h"

// System
#include <stdint.h>

/**********************************************************************************************************************
 * Public Function Prototypes
 **********************************************************************************************************************/

/**
 * \brief Initializes the platform. This function should be called before any other platform functions.
 *        It sets up the necessary hardware and peripherals for the platform to function correctly.
 */
void platform_init(void);

/**
 * \brief  Retrieves a unique identifier for the platform. This can be used for logging, debugging, or any
 *         situation where identifying the specific hardware is necessary.
 * \return A string representing the unique identifier of the platform.
 */
char *platform_get_id(void);

/**
 * \brief  Retrieves the current tick count from the platform's timer. This is typically used for timing
 *         operations, measuring elapsed time, or implementing delays.
 * \return The current tick count as a 32-bit unsigned integer.
 */
uint32_t platform_get_tick(void);

#if (CONFIG_TEST_SWO_ENABLED == 1)
/**
 * \brief      Initializes the ARM CoreSight ITM/TPIU front-end for asynchronous SWO output and configures the
 *             platform-specific SWO pin so the test firmware can stream trace data through the debug probe.
 * \param[in]  baudrate  Desired SWO line speed in bits per second. The closest divider achievable from the current
 *                       \c SystemCoreClock is selected automatically.
 */
void platform_swo_init(const uint32_t baudrate);

/**
 * \brief      Sends a byte buffer over ITM stimulus port 0, blocking until each byte is accepted by the SWO FIFO.
 * \param[in]  data  Pointer to the byte buffer to transmit. Must not be NULL when \a size is non-zero.
 * \param[in]  size  Number of bytes to transmit from \a data.
 */
void platform_swo_send(const uint8_t *data, uint16_t size);
#endif

#if (CLI_MODE == CLI_MODE_UART)
/**
 * \brief      Initializes the UART peripheral for CLI communication. This function sets up the UART with the specified
 *             baud rate and configures it for transmitting and receiving data.
 * \param[in]  baudrate  The baud rate to configure the UART peripheral with.
 */
void platform_cli_uart_init(const uint32_t baudrate);

/**
 * \brief      Transmits data over the UART peripheral. This function sends the specified data buffer of a given size
 *             through the UART interface.
 * \param[in]  data  Pointer to the data buffer to be transmitted.
 * \param[in]  size  The size of the data buffer in bytes.
 */
void platform_cli_uart_transmit(const uint8_t *data, uint16_t size);

/**
 * \brief      Callback function for UART receive complete interrupt.
 *             This function should be called in platform code when a byte of data is received
 *             over the UART interface.
 * 
 * \param[in]  data  The byte of data received over UART.
 */
extern void cli_rx_callback(const uint8_t data);

#endif

#endif // PLATFORM_IFACE_H_
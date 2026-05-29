/**
 * \file   main.c
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Main source file
 * \note   This file contains the main entry point for the application.
 */


/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

#include "platform_iface.h"
#include "platform_config.h"

#if (CLI_MODE != CLI_MODE_DISABLED)
#define EMBEDDED_CLI_IMPL
#include "embedded_cli.h"
#endif

#include <stdio.h>
#include <string.h>

#if (CLI_MODE == CLI_MODE_RTT)
#include "SEGGER_RTT.h"
#endif


/**********************************************************************************************************************
 * Private Definitions
 **********************************************************************************************************************/

#define CONFIG_TEST_BUFFER_SIZE (256)

/**********************************************************************************************************************
 * Private Data
 **********************************************************************************************************************/

static uint8_t test_buffer_to_overwrite[CONFIG_TEST_BUFFER_SIZE] = { 0 };
static const char test_string_for_buffer_overwrite[] = "MioLink Testing, platform: " PLATFORM_NAME_STRING "." "Buffer Overwrite Test: Lorem ipsum dolor sit amet, consectetur adipiscing elit.";

static char test_buffer_always_same[CONFIG_TEST_BUFFER_SIZE] __attribute__((used)) =
"Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.";

#if (CONFIG_TEST_SWO_ENABLED == 1)
/**
 * \brief Fixed payload emitted on every SWO test tick so the receiver can verify byte-for-byte stability.
 */
static const char test_swo_message[] =
    "MioLink SWO Test, platform: " PLATFORM_NAME_STRING ", same message every iteration.\r\n";
#endif

#if (CLI_MODE != CLI_MODE_DISABLED)
static EmbeddedCli *cli = NULL;
#endif

/**********************************************************************************************************************
 * Private Function Prototypes
 **********************************************************************************************************************/

#if (CLI_MODE != CLI_MODE_DISABLED)
/**
 * \brief  Function to write a character to the CLI output. This function is used as the writeChar callback for the CLI,
 *         allowing it to send characters to the appropriate output based on the configured CLI mode (RTT or UART).
 *
 * \param[in] embeddedCli Pointer to the EmbeddedCli instance that is writing the character.
 * \param[in] c The character to be written to the CLI output.
 */
static void cli_write_char(EmbeddedCli *embeddedCli, char c);

/**
 * \brief  CLI command handler for the "platform" command. This function is called when the "platform" command is executed
 *         in the CLI. It prints general information about the platform to the CLI output.
 *
 * \param[in] cli Pointer to the EmbeddedCli instance that executed the command.
 * \param[in] args String of arguments passed with the command (not used in this handler).
 * \param[in] context Pointer to any specific context that may be required for this command (not used in this handler).
 */
static void cli_platform_handler(EmbeddedCli *cli, char *args, void *context);

/**
 * \brief  CLI command handler for the "id" command. This function is called when the "id" command is executed in the CLI.
 *         It retrieves and prints the unique identifier of the platform to the CLI output.
 *
 * \param[in] cli Pointer to the EmbeddedCli instance that executed the command.
 * \param[in] args String of arguments passed with the command (not used in this handler).
 * \param[in] context Pointer to any specific context that may be required for this command (not used in this handler).
 */
static void cli_id_handler(EmbeddedCli *cli, char *args, void *context);
#endif

/**
 * \brief  Test function to check for buffer overwriting. It copies a predefined string into a buffer that is intended
 *         to be overwritten, allowing us to verify if the buffer is being overwritten correctly during tests.
 */
static void test_buffer_overwrite(void);

#if (CONFIG_TEST_SWO_ENABLED == 1)
/**
 * \brief  Emits the fixed \c test_swo_message buffer over the SWO line each time the configured period
 *         (\c CONFIG_TEST_SWO_PERIOD_MS) elapses since the previous emission. Called from the main loop and uses
 *         \c platform_get_tick() to compare elapsed time, so it is safe against tick wrap-around.
 */
static void test_swo_periodic(void);
#endif

/**
 * \brief  Function to run various tests in the main loop. This function is called repeatedly in the main loop and can
 *         be used to perform any necessary testing or operations that need to be executed continuously.
 */
static void run_tests(void);

/**********************************************************************************************************************
 * Private Functions
 **********************************************************************************************************************/

#if (CLI_MODE != CLI_MODE_DISABLED)
static void cli_write_char(EmbeddedCli *embeddedCli, char c)
{
#if (CLI_MODE == CLI_MODE_RTT)
    SEGGER_RTT_PutCharSkipNoLock(CONFIG_TEST_RTT_CHANNEL, c);
#elif (CLI_MODE == CLI_MODE_UART)
    platform_cli_uart_transmit((const uint8_t *)&c, 1);
#endif
}

static void cli_platform_handler(EmbeddedCli *cli, char *args, void *context)
{
    embeddedCliPrint(cli, "Platform: " PLATFORM_NAME_STRING);
}

static void cli_id_handler(EmbeddedCli *cli, char *args, void *context)
{
    embeddedCliPrint(cli, platform_get_id());
}
#endif

static void test_buffer_overwrite(void)
{
    memcpy(test_buffer_to_overwrite, test_string_for_buffer_overwrite, sizeof(test_string_for_buffer_overwrite));
}

#if (CONFIG_TEST_SWO_ENABLED == 1)
static void test_swo_periodic(void)
{
    static uint32_t last_tick = 0;
    const uint32_t now = platform_get_tick();

    if ((uint32_t)(now - last_tick) >= (uint32_t)CONFIG_TEST_SWO_PERIOD_MS)
    {
        last_tick = now;
        platform_swo_send((const uint8_t *)test_swo_message, (uint16_t)(sizeof(test_swo_message) - 1U));
    }
}
#endif

static void run_tests(void)
{
    volatile char dummy = test_buffer_always_same[0]; // prevent optimization of test_buffer_always_same
    (void)dummy; // prevent unused variable warning

    test_buffer_overwrite();
#if (CONFIG_TEST_SWO_ENABLED == 1)
    test_swo_periodic();
#endif
}

/**********************************************************************************************************************
 * Public Functions
 **********************************************************************************************************************/

#if (CLI_MODE == CLI_MODE_UART)
void cli_rx_callback(const uint8_t data)
{
    if (cli != NULL)
    {
        embeddedCliReceiveChar(cli, data);
    }
}
#endif

int main(void)
{
    platform_init();

#if (CONFIG_TEST_SWO_ENABLED == 1)
    platform_swo_init(CONFIG_TEST_SWO_BAUDRATE);
#endif

#if (CLI_MODE == CLI_MODE_RTT)
    SEGGER_RTT_Init();
#elif (CLI_MODE == CLI_MODE_UART)
    platform_cli_uart_init(CONFIG_TEST_UART_BAUDRATE);
#endif

#if (CLI_MODE != CLI_MODE_DISABLED)
    EmbeddedCliConfig cli_config = { 0 };
    memcpy(&cli_config, embeddedCliDefaultConfig(), sizeof(cli_config));
    cli_config.enableAutoComplete = false; // disable autocompletion to simplify testing

    cli = embeddedCliNew(&cli_config);

    if (cli == NULL)
    {
        while (1);
    }
    cli->writeChar = cli_write_char;

    CliCommandBinding id_binding = {"id", "Print unique platform ID", 0, NULL, cli_id_handler};
    CliCommandBinding platform_binding = {"platform", "Print general platform info", 0, NULL, cli_platform_handler};

    embeddedCliAddBinding(cli, id_binding);
    embeddedCliAddBinding(cli, platform_binding);
#endif

    while (1)
    {
        run_tests();
#if (CLI_MODE != CLI_MODE_DISABLED)
        embeddedCliProcess(cli);
#endif
    }

    return 0;
}
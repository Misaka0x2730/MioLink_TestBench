cmake_minimum_required(VERSION 3.22)

# Common settings
set(CMAKE_C_STANDARD                11)
set(CMAKE_C_STANDARD_REQUIRED       ON)
set(CMAKE_C_EXTENSIONS              ON)
set(CMAKE_CXX_STANDARD              17)
set(CMAKE_CXX_STANDARD_REQUIRED     ON)
set(CMAKE_CXX_EXTENSIONS            ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS	ON)
set(CMAKE_TOOLCHAIN_FILE			${CMAKE_CURRENT_LIST_DIR}/cmake/gcc-arm-none-eabi.cmake)
set(CMAKE_C_COMPILER_FORCED TRUE)
set(CMAKE_CXX_COMPILER_FORCED TRUE)

project(MioLink_TestBench)
enable_language(C CXX ASM)

# CLI backend selection. Choose one of:
#   UART - CLI runs over a platform UART (default)
#   RTT  - CLI runs over SEGGER RTT
#   NONE - CLI is fully disabled (no embedded-cli, no RTT, no UART pulled in)
set(CONFIG_CLI "UART" CACHE STRING "CLI backend: UART, RTT, or NONE")
set_property(CACHE CONFIG_CLI PROPERTY STRINGS "UART" "RTT" "NONE")

# Periodic SWO trace output test. When enabled, the test firmware emits a
# fixed message over the ARM ITM/SWO trace pin at a configurable interval.
option(CONFIG_TEST_SWO "Enable periodic SWO trace output test" OFF)
set(CONFIG_TEST_SWO_BAUDRATE "2250000" CACHE STRING "SWO trace baud rate in Hz")
set(CONFIG_TEST_SWO_PERIOD_MS "1000" CACHE STRING "Period between SWO test messages in ms")

# Common platform interface
add_library(platform_common INTERFACE)
target_include_directories(platform_common INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/platform_common
)

if(CONFIG_CLI STREQUAL "UART")
    target_compile_definitions(platform_common INTERFACE
        CONFIG_CLI_USE_UART=1
    )
elseif(CONFIG_CLI STREQUAL "RTT")
    target_compile_definitions(platform_common INTERFACE
        CONFIG_CLI_USE_RTT=1
    )
elseif(CONFIG_CLI STREQUAL "NONE")
    target_compile_definitions(platform_common INTERFACE
        CONFIG_CLI_DISABLED=1
    )
else()
    message(FATAL_ERROR
        "Invalid CONFIG_CLI value: '${CONFIG_CLI}'. Must be one of: UART, RTT, NONE."
    )
endif()

message(STATUS "CLI backend: ${CONFIG_CLI}")

if(CONFIG_TEST_SWO)
    target_compile_definitions(platform_common INTERFACE
        CONFIG_TEST_SWO_ENABLED=1
        CONFIG_TEST_SWO_BAUDRATE=${CONFIG_TEST_SWO_BAUDRATE}
        CONFIG_TEST_SWO_PERIOD_MS=${CONFIG_TEST_SWO_PERIOD_MS}
    )
    message(STATUS "SWO test: enabled (baud=${CONFIG_TEST_SWO_BAUDRATE} Hz, period=${CONFIG_TEST_SWO_PERIOD_MS} ms)")
else()
    target_compile_definitions(platform_common INTERFACE
        CONFIG_TEST_SWO_ENABLED=0
    )
    message(STATUS "SWO test: disabled")
endif()

# Add external dependencies (CMSIS, HAL, embedded-cli, etc.)
add_subdirectory(external)

include(cmake/platform-helpers.cmake)

# Scan for platforms directory
set(PLATFORM_DIR ${CMAKE_CURRENT_LIST_DIR}/platforms)
message(STATUS "Scanning for platforms in ${PLATFORM_DIR}")

find_platforms(${PLATFORM_DIR} PLATFORMS_LIST)

if(NOT PLATFORMS_LIST)
    message(FATAL_ERROR "No platforms found in ${PLATFORM_DIR}")
endif()

message(STATUS "Found platforms: ${PLATFORMS_LIST}")

# Function to add a test board target
function(add_test_board PLATFORM_NAME)
    set(TARGET_NAME test_board_${PLATFORM_NAME})
    set(PLATFORM_LIB platform_${PLATFORM_NAME})

    add_executable(${TARGET_NAME}
        ${CMAKE_CURRENT_LIST_DIR}/application/main.c
    )

    # Output each platform build into its own subdirectory in the build tree
    set_target_properties(${TARGET_NAME} PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/test_boards/${TARGET_NAME}
        ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/test_boards/${TARGET_NAME}
        LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/test_boards/${TARGET_NAME}
    )

    target_compile_definitions(${TARGET_NAME} PRIVATE
        PLATFORM_NAME_STRING="${PLATFORM_NAME}"
    )

    target_link_libraries(${TARGET_NAME} PRIVATE
        ${PLATFORM_LIB}
        platform_common
    )

    if(NOT CONFIG_CLI STREQUAL "NONE")
        target_link_libraries(${TARGET_NAME} PRIVATE embedded_cli)
    endif()

    if(CONFIG_CLI STREQUAL "RTT")
        target_link_libraries(${TARGET_NAME} PRIVATE segger_rtt)
    endif()

    # Place the .map file in the same directory as the executable
    target_link_options(${TARGET_NAME} PRIVATE
        -Wl,-Map=$<TARGET_FILE_DIR:${TARGET_NAME}>/${TARGET_NAME}.map
    )

    list(APPEND TEST_BOARD_TARGETS ${TARGET_NAME})
    set(TEST_BOARD_TARGETS "${TEST_BOARD_TARGETS}" PARENT_SCOPE)
endfunction()

# Check if a specific platform was requested via command line
if(PLATFORM)
    list(FIND PLATFORMS_LIST ${PLATFORM} PLATFORM_INDEX)
    if(PLATFORM_INDEX EQUAL -1)
        message(FATAL_ERROR "Unknown platform: ${PLATFORM}")
    endif()

    add_subdirectory(${PLATFORM_DIR}/${PLATFORM})
    add_test_board(${PLATFORM})
else()
    foreach(platform_name IN LISTS PLATFORMS_LIST)
        add_subdirectory(${PLATFORM_DIR}/${platform_name})
        add_test_board(${platform_name})
    endforeach()
endif()

message(STATUS "Configured test board targets: ${TEST_BOARD_TARGETS}")
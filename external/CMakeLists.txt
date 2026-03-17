# CMSIS_6 includes
add_library(cmsis_6 INTERFACE)
target_include_directories(cmsis_6 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/CMSIS_6/CMSIS/Core/Include
)

# CMSIS device STM32F1 library
add_library(cmsis_device_f1 INTERFACE)

target_sources(cmsis_device_f1 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f1/Source/Templates/system_stm32f1xx.c)

target_include_directories(cmsis_device_f1 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f1/Include
)

set(CMSIS_F1_STARTUP_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f1/Source/Templates/gcc PARENT_SCOPE)
set(CMSIS_F1_LINKER_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f1/Source/Templates/gcc/linker PARENT_SCOPE)

# STM32F1xx HAL driver
add_library(stm32f1xx_hal_driver INTERFACE)

file(GLOB stm32f1xx_hal_sources CONFIGURE_DEPENDS ${CMAKE_CURRENT_LIST_DIR}/stm32f1xx-hal-driver/Src/*.c)
list(FILTER stm32f1xx_hal_sources EXCLUDE REGEX ".*template\\.c$")

target_sources(stm32f1xx_hal_driver INTERFACE
    ${stm32f1xx_hal_sources})

target_include_directories(stm32f1xx_hal_driver INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/stm32f1xx-hal-driver/Inc
    ${CMAKE_CURRENT_LIST_DIR}/stm32f1xx-hal-driver/Inc/Legacy
)

# CMSIS device STM32F4 library
add_library(cmsis_device_f4 INTERFACE)

target_sources(cmsis_device_f4 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f4/Source/Templates/system_stm32f4xx.c)

target_include_directories(cmsis_device_f4 INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f4/Include
)

set(CMSIS_F4_STARTUP_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}/cmsis-device-f4/Source/Templates/gcc PARENT_SCOPE)

# STM32F4xx HAL driver
add_library(stm32f4xx_hal_driver INTERFACE)

file(GLOB stm32f4xx_hal_sources CONFIGURE_DEPENDS ${CMAKE_CURRENT_LIST_DIR}/stm32f4xx-hal-driver/Src/*.c)
list(FILTER stm32f4xx_hal_sources EXCLUDE REGEX ".*template\\.c$")

target_sources(stm32f4xx_hal_driver INTERFACE
    ${stm32f4xx_hal_sources})

target_include_directories(stm32f4xx_hal_driver INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/stm32f4xx-hal-driver/Inc
    ${CMAKE_CURRENT_LIST_DIR}/stm32f4xx-hal-driver/Inc/Legacy
)

# Generate embedded-cli single-header
find_package(Python3 COMPONENTS Interpreter REQUIRED)

set(EMBEDDED_CLI_LIB_DIR ${CMAKE_CURRENT_LIST_DIR}/embedded-cli/lib)
set(EMBEDDED_CLI_OUT_DIR ${EMBEDDED_CLI_LIB_DIR}/shl)
set(EMBEDDED_CLI_OUT_HEADER ${EMBEDDED_CLI_OUT_DIR}/embedded_cli.h)

add_custom_command(
    OUTPUT ${EMBEDDED_CLI_OUT_HEADER}
    COMMAND ${Python3_EXECUTABLE} ${EMBEDDED_CLI_LIB_DIR}/build-shl.py
    WORKING_DIRECTORY ${EMBEDDED_CLI_LIB_DIR}
    DEPENDS
        ${EMBEDDED_CLI_LIB_DIR}/include/embedded_cli.h
        ${EMBEDDED_CLI_LIB_DIR}/src/embedded_cli.c
    COMMENT "Generating embedded-cli single-header: ${EMBEDDED_CLI_OUT_HEADER}"
    VERBATIM
)

add_custom_target(embedded_cli_single_header DEPENDS ${EMBEDDED_CLI_OUT_HEADER})

# embedded-cli library target (header-only)
add_library(embedded_cli INTERFACE)

target_include_directories(embedded_cli INTERFACE
    ${EMBEDDED_CLI_OUT_DIR}
)

add_dependencies(embedded_cli embedded_cli_single_header)

# Segger RTT library
add_library(segger_rtt INTERFACE)
target_sources(segger_rtt INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/RTT/RTT/SEGGER_RTT.c
)

target_include_directories(segger_rtt INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/RTT/Config
    ${CMAKE_CURRENT_LIST_DIR}/RTT/RTT
)
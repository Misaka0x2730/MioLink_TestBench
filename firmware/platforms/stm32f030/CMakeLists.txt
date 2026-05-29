# MCU Parameters
set(MCU_PARAMETERS
    -mthumb
    -mcpu=cortex-m0
    -mfloat-abi=soft
)

set(STARTUP_FILE ${CMSIS_F0_STARTUP_DIRECTORY}/startup_stm32f030x6.s)

set(COMPILE_OPTIONS
    ${MCU_PARAMETERS}
    -Wall
    -Wno-unused-parameter
    -Og
)

set(COMPILE_DEFINITIONS
    "USE_HAL_DRIVER"
    "STM32F030x6"
)

set(LINK_LIBS
    cmsis_6
    cmsis_device_f0
    stm32f0xx_hal_driver
    platform_common
)

set(LINK_OPTIONS
    ${MCU_PARAMETERS}
    -specs=nosys.specs
    -specs=nano.specs
    -Wl,--gc-sections
    -Wl,--start-group
    -lc
    -lm
    -Wl,--end-group
    -Wl,--print-memory-usage
)

set(LINKER_SCRIPT ${CMAKE_CURRENT_LIST_DIR}/linker/STM32F030F4Px_FLASH.ld)

# Create platform target using reusable function from cmake/gcc-arm-none-eabi.cmake
create_platform_target(${CMAKE_CURRENT_LIST_DIR}
    STARTUP ${STARTUP_FILE}
    SOURCE_DIRS ${CMAKE_CURRENT_LIST_DIR}/system
    COMPILE_OPTIONS ${COMPILE_OPTIONS}
    COMPILE_DEFINITIONS ${COMPILE_DEFINITIONS}
    LINK_LIBS ${LINK_LIBS}
    LINK_OPTIONS ${LINK_OPTIONS}
    LINKER_SCRIPT ${LINKER_SCRIPT}
)

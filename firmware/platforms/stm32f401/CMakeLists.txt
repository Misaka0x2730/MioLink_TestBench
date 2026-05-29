# MCU parameters
set(MCU_PARAMETERS
    -mthumb
    -mcpu=cortex-m4
    -mfpu=fpv4-sp-d16
    -mfloat-abi=hard
)

set(STARTUP_FILE ${CMSIS_F4_STARTUP_DIRECTORY}/startup_stm32f401xe.s)

set(COMPILE_OPTIONS
    ${MCU_PARAMETERS}
    -Wall
    -Wno-unused-parameter
)

set(COMPILE_DEFINITIONS
    "USE_HAL_DRIVER"
    "STM32F401xE"
)

set(LINK_LIBS
    cmsis_6
    cmsis_device_f4
    stm32f4xx_hal_driver
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

set(LINKER_SCRIPT ${CMAKE_CURRENT_LIST_DIR}/linker/STM32F401CDUX_FLASH.ld)

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
# MCU parameters
set(MCU_PARAMETERS
    -mthumb
    -mcpu=cortex-m4
    -mfpu=fpv4-sp-d16
    -mfloat-abi=hard
)

set(STARTUP_FILE ${HC32F460_STARTUP_DIRECTORY}/startup_hc32f460.S)

set(COMPILE_OPTIONS
    ${MCU_PARAMETERS}
    -Wall
    -Wno-unused-parameter
)

set(COMPILE_DEFINITIONS
    "USE_DDL_DRIVER"
    "HC32F460"
)

set(LINK_LIBS
    cmsis_device_hc32f460
    hc32f460_ll_driver
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

set(LINKER_SCRIPT ${CMAKE_CURRENT_LIST_DIR}/linker/HC32F460xE.ld)

# Create platform target using reusable function from cmake/platform-helpers.cmake
create_platform_target(${CMAKE_CURRENT_LIST_DIR}
    STARTUP ${STARTUP_FILE}
    SOURCE_DIRS ${CMAKE_CURRENT_LIST_DIR}/system
    COMPILE_OPTIONS ${COMPILE_OPTIONS}
    COMPILE_DEFINITIONS ${COMPILE_DEFINITIONS}
    LINK_LIBS ${LINK_LIBS}
    LINK_OPTIONS ${LINK_OPTIONS}
    LINKER_SCRIPT ${LINKER_SCRIPT}
)

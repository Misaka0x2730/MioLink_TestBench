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

project(MioLink_TestSetup)
enable_language(C CXX ASM)

# Common platform interface
add_library(platform_common INTERFACE)
target_include_directories(platform_common INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/platform_common
)

target_compile_definitions(platform_common INTERFACE
        CONFIG_CLI_USE_UART=1
)

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

# Function to add a test platform target
function(add_test_platform PLATFORM_NAME)
    set(TARGET_NAME test_platform_${PLATFORM_NAME})
    set(PLATFORM_LIB platform_${PLATFORM_NAME})

    add_executable(${TARGET_NAME}
        ${CMAKE_CURRENT_LIST_DIR}/application/main.c
    )

    # Output each platform build into its own subdirectory in the build tree
    set_target_properties(${TARGET_NAME} PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/test_platforms/${TARGET_NAME}
        ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/test_platforms/${TARGET_NAME}
        LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/test_platforms/${TARGET_NAME}
    )

    target_compile_definitions(${TARGET_NAME} PRIVATE
        PLATFORM_NAME_STRING="${PLATFORM_NAME}"
    )

    target_link_libraries(${TARGET_NAME} PRIVATE
        ${PLATFORM_LIB}
        platform_common
        segger_rtt
        embedded_cli
    )

    # Place the .map file in the same directory as the executable
    target_link_options(${TARGET_NAME} PRIVATE
        -Wl,-Map=$<TARGET_FILE_DIR:${TARGET_NAME}>/${TARGET_NAME}.map
    )

    list(APPEND TEST_PLATFORM_TARGETS ${TARGET_NAME})
    set(TEST_PLATFORM_TARGETS "${TEST_PLATFORM_TARGETS}" PARENT_SCOPE)
endfunction()

# Check if a specific platform was requested via command line
if(PLATFORM)
    list(FIND PLATFORMS_LIST ${PLATFORM} PLATFORM_INDEX)
    if(PLATFORM_INDEX EQUAL -1)
        message(FATAL_ERROR "Unknown platform: ${PLATFORM}")
    endif()

    add_subdirectory(${PLATFORM_DIR}/${PLATFORM})
    add_test_platform(${PLATFORM})
else()
    foreach(platform_name IN LISTS PLATFORMS_LIST)
        add_subdirectory(${PLATFORM_DIR}/${platform_name})
        add_test_platform(${platform_name})
    endforeach()
endif()

message(STATUS "Configured test platform targets: ${TEST_PLATFORM_TARGETS}")
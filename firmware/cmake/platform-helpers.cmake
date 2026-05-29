# Function to gather platforms from a top-level platforms directory
function(find_platforms PLATFORM_DIR OUT_PLATFORMS)
    file(GLOB PLATFORM_ENTRIES CONFIGURE_DEPENDS RELATIVE ${PLATFORM_DIR} ${PLATFORM_DIR}/*)
    set(_found_platforms "")
    foreach(entry IN LISTS PLATFORM_ENTRIES)
        if(IS_DIRECTORY ${PLATFORM_DIR}/${entry})
            list(APPEND _found_platforms ${entry})
        endif()
    endforeach()
    set(${OUT_PLATFORMS} "${_found_platforms}" PARENT_SCOPE)
endfunction()

# Function to create a platform library target from a platform folder
function(create_platform_target PLATFORM_DIR)
    cmake_parse_arguments(PLATFORM "" "STARTUP;LINKER_SCRIPT" "INCLUDE_DIRS;SOURCE_DIRS;COMPILE_OPTIONS;COMPILE_DEFINITIONS;LINK_LIBS;LINK_OPTIONS" ${ARGN})

    get_filename_component(PLATFORM_NAME ${PLATFORM_DIR} NAME)
    set(TARGET_NAME "platform_${PLATFORM_NAME}")

    set(PLATFORM_SOURCES)
    file(GLOB PLATFORM_SOURCE_FILES_LIST CONFIGURE_DEPENDS
        ${PLATFORM_DIR}/platform/*.c
    )
    list(APPEND PLATFORM_SOURCES ${PLATFORM_SOURCE_FILES_LIST})

    if(PLATFORM_SOURCE_DIRS)
        foreach(SRC_DIR ${PLATFORM_SOURCE_DIRS})
            file(GLOB SRC_DIR_SOURCES CONFIGURE_DEPENDS
                ${SRC_DIR}/*.c
            )
            list(APPEND PLATFORM_SOURCES ${SRC_DIR_SOURCES})
        endforeach()
    endif()

    if(NOT PLATFORM_SOURCES)
        message(FATAL_ERROR "create_platform_target: no source files found in ${PLATFORM_DIR}/platform and SOURCE_DIRS")
    endif()

    if(NOT PLATFORM_STARTUP)
        message(FATAL_ERROR "create_platform_target: STARTUP is required")
    endif()

    if(NOT PLATFORM_LINKER_SCRIPT)
        message(FATAL_ERROR "create_platform_target: LINKER_SCRIPT is required")
    endif()

    add_library(${TARGET_NAME} OBJECT
        ${PLATFORM_SOURCES}
        ${PLATFORM_STARTUP}
    )

    target_include_directories(${TARGET_NAME} PRIVATE
        ${PLATFORM_DIR}/config
    )

    target_include_directories(${TARGET_NAME} PUBLIC
        ${PLATFORM_DIR}/platform
    )

    if(PLATFORM_INCLUDE_DIRS)
        target_include_directories(${TARGET_NAME} PUBLIC ${PLATFORM_INCLUDE_DIRS})
    endif()

    if(PLATFORM_COMPILE_OPTIONS)
        target_compile_options(${TARGET_NAME} PUBLIC ${PLATFORM_COMPILE_OPTIONS})
    endif()

    if(PLATFORM_COMPILE_DEFINITIONS)
        target_compile_definitions(${TARGET_NAME} PRIVATE ${PLATFORM_COMPILE_DEFINITIONS})
    endif()

    if(PLATFORM_LINK_LIBS)
        target_link_libraries(${TARGET_NAME} PRIVATE ${PLATFORM_LINK_LIBS})
    endif()

    if(PLATFORM_LINK_OPTIONS)
        target_link_options(${TARGET_NAME} INTERFACE
            -T${PLATFORM_LINKER_SCRIPT}
            ${PLATFORM_LINK_OPTIONS}
        )
    else()
        target_link_options(${TARGET_NAME} INTERFACE
            -T${PLATFORM_LINKER_SCRIPT}
        )
    endif()

    # Export target name if caller wants it
    set(${TARGET_NAME}_NAME ${TARGET_NAME} PARENT_SCOPE)
endfunction()
/**
 * \file   platform.h
 * \author Misaka0x2730 (Dmitry Rezvanov)
 * \brief  Platform header file
 * \note   This file defines the platform-specific functions and includes necessary headers for the HC32F460 platform.
 */

#ifndef PLATFORM_H_
#define PLATFORM_H_

/**********************************************************************************************************************
 * Includes
 **********************************************************************************************************************/

// Platform config
#include "platform_config.h"

// HC32F460 DDL includes
#include "hc32_ll.h"

// System
#include <stdint.h>

/**********************************************************************************************************************
 * Public Function Prototypes
 **********************************************************************************************************************/

void SysTick_Handler(void);

#endif // PLATFORM_H_

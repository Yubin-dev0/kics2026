/* safety.h - integer port of the A1-validated safety rule (sim/a1_controller.py). */
#ifndef SAFETY_H
#define SAFETY_H

#include <stdint.h>

/* Thresholds are sensor-referenced (LDS-01 sits 32 mm behind base_link). */
#define SAFETY_D_STOP_MM 200
#define SAFETY_D_SLOW_MM 400
#define SAFETY_V_MAX_MMS 220
#define SAFETY_V_FLOOR_MMS 100

typedef enum {
    ST_RUN = 0,
    ST_SLOW = 1,
    ST_STOP = 2,
    ST_WDOG = 3
} safety_state_t;

/* Returns the state and writes the commanded linear speed in mm/s.
 * Integer division truncates, so v is never faster than the float rule. */
safety_state_t safety_eval(uint16_t min_mm, int16_t *v_mms);

#endif

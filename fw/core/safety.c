/* safety.c - see safety.h */
#include "safety.h"

safety_state_t safety_eval(uint16_t min_mm, int16_t *v_mms)
{
    if (min_mm >= SAFETY_D_SLOW_MM) {
        *v_mms = SAFETY_V_MAX_MMS;
        return ST_RUN;
    }
    if (min_mm >= SAFETY_D_STOP_MM) {
        int32_t v = (int32_t)SAFETY_V_MAX_MMS * (min_mm - SAFETY_D_STOP_MM) /
                    (SAFETY_D_SLOW_MM - SAFETY_D_STOP_MM);
        if (v < SAFETY_V_FLOOR_MMS) v = SAFETY_V_FLOOR_MMS;
        *v_mms = (int16_t)v;
        return ST_SLOW;
    }
    *v_mms = 0; /* includes min_mm == 0 (all rays invalid) */
    return ST_STOP;
}

/* fw_sources.c - compiles the shared sources into this CubeIDE project.
 *
 * fw/core is also built and unit-tested on the host (fw/host). Pulling it in by relative
 * #include keeps a single copy of every file and needs no linked folders or extra include
 * paths, which STM32CubeMX code generation can remove.
 *
 * Paths are relative to this file: fw/stm32/n2_safety/Core/Src/
 */
#include "../../../../core/proto.c"
#include "../../../../core/safety.c"
#include "../../../../core/mode.c"
#include "../../../../core/app.c"
#include "../../../port/app_port.c"

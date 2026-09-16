# Host build of fw/core and the STM32 port: unit tests, the UART receive simulation,
# and the tty-backed fake board.
CORE := ../core
PORT := ../stm32/port
CFLAGS := -std=c11 -Wall -Wextra -Werror -O2 -I$(CORE)
SRC := $(CORE)/proto.c $(CORE)/safety.c $(CORE)/mode.c $(CORE)/app.c

all: test_core rx_sim fake_stm32

test_core: test_core.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^

rx_sim: rx_sim.c $(PORT)/app_port.c $(SRC) hal_stub/main.h
	$(CC) $(CFLAGS) -Ihal_stub -I$(PORT) -o $@ rx_sim.c $(PORT)/app_port.c $(SRC)

fake_stm32: fake_stm32.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^

# builds the fake board too, so a stale fake_stm32 never outlives a protocol change
test: test_core rx_sim fake_stm32
	./test_core
	./rx_sim

clean:
	rm -f test_core rx_sim fake_stm32

.PHONY: all test clean

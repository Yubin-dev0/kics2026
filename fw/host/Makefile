# Host build of fw/core: unit tests and the tty-backed fake board.
CORE := ../core
CFLAGS := -std=c11 -Wall -Wextra -Werror -O2 -I$(CORE)
SRC := $(CORE)/proto.c $(CORE)/safety.c $(CORE)/mode.c $(CORE)/app.c

all: test_core fake_stm32

test_core: test_core.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^

fake_stm32: fake_stm32.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^

test: test_core
	./test_core

clean:
	rm -f test_core fake_stm32

.PHONY: all test clean

CC = gcc
CFLAGS = -O2 -g -std=c11 -Wall -Wextra -Werror

.PHONY: all test clean
all: build/memory_phases

build/memory_phases: workloads/memory_phases.c
	mkdir -p build
	$(CC) $(CFLAGS) $< -o $@

test: all
	python3 -m unittest discover -s tests -v

clean:
	rm -f build/memory_phases

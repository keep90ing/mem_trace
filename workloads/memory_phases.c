#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

/* Independent ground truth: two mmap regions, phases on CLOCK_MONOTONIC,
 * pagemap snapshots before and after all access phases. No perf dependency. */
static uint64_t now_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) { perror("clock_gettime"); exit(1); }
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static void pages(FILE *f, const char *name, const char *when, void *p, size_t size, size_t page) {
    int fd = open("/proc/self/pagemap", O_RDONLY);
    for (size_t i = 0; i < size / page; ++i) {
        uint64_t entry = 0, pfn = 0;
        off_t off = (off_t)(((uintptr_t)p / page + i) * 8);
        if (fd >= 0 && pread(fd, &entry, sizeof(entry), off) == sizeof(entry)
            && (entry & (1ULL << 63))) pfn = entry & ((1ULL << 55) - 1);
        fprintf(f, "{\"type\":\"page\",\"region\":\"%s\",\"when\":\"%s\",\"index\":%zu,\"pa\":",
                name, when, i);
        if (pfn) fprintf(f, "\"0x%" PRIx64 "\"", pfn * page);
        else fputs("null", f); /* PFN hidden or unavailable; never fake a PA. */
        fputs("}\n", f);
    }
    if (fd >= 0) close(fd);
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s MANIFEST.jsonl SECONDS_PER_PHASE\n", argv[0]);
        return 2;
    }
    char *end;
    double seconds = strtod(argv[2], &end);
    if (*end || !isfinite(seconds) || seconds <= 0 || seconds > 3600) {
        fputs("seconds must be in (0, 3600]\n", stderr); return 2;
    }
    FILE *f = fopen(argv[1], "wx");
    if (!f) { perror("manifest"); return 1; }
    prctl(PR_SET_NAME, "ibs-memory", 0, 0, 0);
    const size_t size = 16 * 1024 * 1024, page = (size_t)sysconf(_SC_PAGESIZE);
    volatile unsigned char *regions[2];
    const char *names[2] = {"A", "B"};
    for (int i = 0; i < 2; ++i) {
        void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p == MAP_FAILED) { perror("mmap"); return 1; }
        madvise(p, size, MADV_NOHUGEPAGE);
        memset(p, 1, size); /* fault and dirty all pages before measured phases */
        regions[i] = p;
        int locked = mlock(p, size) == 0;
        fprintf(f, "{\"type\":\"region\",\"name\":\"%s\",\"pid\":%d,\"va\":\"0x%" PRIxPTR
                "\",\"size\":%zu,\"page_size\":%zu,\"mlocked\":%s}\n",
                names[i], getpid(), (uintptr_t)p, size, page, locked ? "true" : "false");
        pages(f, names[i], "before", p, size, page);
    }
    fflush(f);
    const char *labels[] = {"small_A", "large_A", "random_B", "small_A_again"};
    const int which[] = {0, 0, 1, 0};
    const size_t active[] = {1024*1024, size, size, 1024*1024};
    uint64_t checksum = 0, rng = 42;
    for (int phase = 0; phase < 4; ++phase) {
        volatile unsigned char *p = regions[which[phase]];
        size_t mask = active[phase] - 1, index = 0;
        uint64_t start = now_ns(), deadline = start + (uint64_t)(seconds * 1e9);
        do {
            for (int j = 0; j < 8192; ++j) {
                if (phase == 2) {
                    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
                    index = ((size_t)rng & mask) & ~(size_t)63;
                } else index = (index + 64) & mask;
                p[index] = (unsigned char)(p[index] + 1);
                checksum += p[index];
            }
        } while (now_ns() < deadline);
        uint64_t finish = now_ns();
        fprintf(f, "{\"type\":\"phase\",\"name\":\"%s\",\"region\":\"%s\",\"pid\":%d,"
                "\"start_ns\":%" PRIu64 ",\"end_ns\":%" PRIu64 ",\"active_bytes\":%zu}\n",
                labels[phase], names[which[phase]], getpid(), start, finish, active[phase]);
        fflush(f);
    }
    for (int i = 0; i < 2; ++i) {
        pages(f, names[i], "after", (void *)regions[i], size, page);
        munmap((void *)regions[i], size);
    }
    if (fclose(f)) { perror("fclose"); return 1; }
    printf("checksum=%" PRIu64 "\n", checksum);
    return 0;
}

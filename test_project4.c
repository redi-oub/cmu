/*
 * test_project4.c
 *
 * Automated test harness for Project4.
 * Loads the same input file, performs the same lookup logic,
 * and checks 30 test cases covering all code paths.
 *
 * Compile: gcc -Wall -Wextra -o test_project4 test_project4.c
 * Run:     ./test_project4 "Project4Input .txt"
 */

#include <stdio.h>
#include <string.h>

/* Address width assumptions — must match Project4.c */
#define VPO_BITS     6
#define VPN_BITS     8
#define PPO_BITS     VPO_BITS
#define PPN_BITS     6
#define CO_BITS      2
#define CI_BITS      4
#define CT_BITS      (PPN_BITS + PPO_BITS - CO_BITS - CI_BITS)

#define VPO_MASK     ((1 << VPO_BITS) - 1)
#define VPN_MASK     ((1 << VPN_BITS) - 1)
#define CO_MASK      ((1 << CO_BITS) - 1)
#define CI_MASK      ((1 << CI_BITS) - 1)
#define CT_MASK      ((1 << CT_BITS) - 1)

#define TLB_SETS     4
#define TLB_WAYS     2
#define TLBI_BITS    2
#define TLBT_BITS    (VPN_BITS - TLBI_BITS)
#define TLBI_MASK    ((1 << TLBI_BITS) - 1)
#define TLBT_MASK    ((1 << TLBT_BITS) - 1)

#define PT_SIZE      (1 << VPN_BITS)
#define CACHE_SETS   (1 << CI_BITS)
#define CACHE_BLOCK  (1 << CO_BITS)
#define LINE_MAX     256

typedef struct { int tag; int ppn; } TLBEntry;
typedef struct { int valid; int tag; int data[CACHE_BLOCK]; } CacheEntry;

/* Test case: va = virtual address, expected = byte value or -1 for "Can not be determined" */
typedef struct {
    unsigned int va;
    int expected;       /* -1 means "Can not be determined" */
    const char *desc;
} TestCase;

#define UNDETERMINED -1

/* Global data structures (same as Project4.c) */
static TLBEntry tlb[TLB_SETS][TLB_WAYS];
static int tlbCount[TLB_SETS];
static int pageTable[PT_SIZE];
static int pageTableValid[PT_SIZE];
static CacheEntry cache[CACHE_SETS];

/*
 * lookup - Perform the full virtual address translation and cache lookup.
 * Returns the byte value (0-255) on success, or -1 if it cannot be determined.
 */
static int lookup(unsigned int va) {
    unsigned int vpn, vpo, tlbi, tlbt;
    unsigned int ppn_found = 0;
    int found = 0;
    unsigned int pa, co, ci, ct;
    int j;

    vpn = (va >> VPO_BITS) & VPN_MASK;
    vpo = va & VPO_MASK;

    tlbi = vpn & TLBI_MASK;
    tlbt = (vpn >> TLBI_BITS) & TLBT_MASK;

    /* TLB lookup */
    for (j = 0; j < tlbCount[tlbi]; j++) {
        if (tlb[tlbi][j].tag == (int)tlbt) {
            ppn_found = tlb[tlbi][j].ppn;
            found = 1;
            break;
        }
    }

    /* Page table fallback */
    if (!found) {
        if ((int)vpn < PT_SIZE && pageTableValid[vpn]) {
            ppn_found = pageTable[vpn];
            found = 1;
        }
    }

    if (!found)
        return UNDETERMINED;

    /* Physical address */
    pa = (ppn_found << VPO_BITS) | vpo;
    co = pa & CO_MASK;
    ci = (pa >> CO_BITS) & CI_MASK;
    ct = (pa >> (CO_BITS + CI_BITS)) & CT_MASK;

    /* Cache lookup */
    if (cache[ci].valid && cache[ci].tag == (int)ct)
        return cache[ci].data[co];

    return UNDETERMINED;
}

/*
 * load_data - Parse the input file and populate TLB, page table, and cache.
 * Returns 0 on success, 1 on failure.
 */
static int load_data(const char *filename) {
    FILE *fp;
    char line[LINE_MAX];
    int i, j;

    for (i = 0; i < TLB_SETS; i++) {
        tlbCount[i] = 0;
        for (j = 0; j < TLB_WAYS; j++) {
            tlb[i][j].tag = 0;
            tlb[i][j].ppn = 0;
        }
    }
    for (i = 0; i < PT_SIZE; i++) {
        pageTable[i] = 0;
        pageTableValid[i] = 0;
    }
    for (i = 0; i < CACHE_SETS; i++) {
        cache[i].valid = 0;
        cache[i].tag = 0;
        for (j = 0; j < CACHE_BLOCK; j++)
            cache[i].data[j] = 0;
    }

    fp = fopen(filename, "r");
    if (fp == NULL) {
        fprintf(stderr, "Error: could not open file %s\n", filename);
        return 1;
    }

    while (fgets(line, LINE_MAX, fp) != NULL) {
        int setIdx, tag, ppn, vpn;
        int cacheIdx, d0, d1, d2, d3;

        if (strncmp(line, "TLB,", 4) == 0) {
            if (sscanf(line, "TLB,%d,%x,%x", &setIdx, &tag, &ppn) == 3) {
                if (setIdx >= 0 && setIdx < TLB_SETS &&
                    tlbCount[setIdx] < TLB_WAYS) {
                    tlb[setIdx][tlbCount[setIdx]].tag = tag;
                    tlb[setIdx][tlbCount[setIdx]].ppn = ppn;
                    tlbCount[setIdx]++;
                }
            }
        } else if (strncmp(line, "Page,", 5) == 0) {
            if (sscanf(line, "Page,%x,%x", &vpn, &ppn) == 2) {
                if (vpn >= 0 && vpn < PT_SIZE) {
                    pageTable[vpn] = ppn;
                    pageTableValid[vpn] = 1;
                }
            }
        } else if (strncmp(line, "Cache,", 6) == 0) {
            if (sscanf(line, "Cache,%x,%x,%x,%x,%x,%x",
                        &cacheIdx, &tag, &d0, &d1, &d2, &d3) == 6) {
                if (cacheIdx >= 0 && cacheIdx < CACHE_SETS) {
                    cache[cacheIdx].valid = 1;
                    cache[cacheIdx].tag = tag;
                    cache[cacheIdx].data[0] = d0;
                    cache[cacheIdx].data[1] = d1;
                    cache[cacheIdx].data[2] = d2;
                    cache[cacheIdx].data[3] = d3;
                }
            }
        }
    }

    fclose(fp);
    return 0;
}

int main(int argc, char *argv[]) {
    int pass = 0, fail = 0, i;
    int result;

    TestCase tests[] = {
        /* --- TLB hit + Cache hit --- */
        /* TLB(set1, tag03 -> PPN 2D), Cache(A, tag 2D): [93,15,DA,3B] */
        { 0x368, 0x93, "TLB hit(set1,tag03->2D), Cache hit(A,2D) CO=0" },
        { 0x369, 0x15, "TLB hit(set1,tag03->2D), Cache hit(A,2D) CO=1" },
        { 0x36A, 0xDA, "TLB hit(set1,tag03->2D), Cache hit(A,2D) CO=2" },
        { 0x36B, 0x3B, "TLB hit(set1,tag03->2D), Cache hit(A,2D) CO=3" },

        /* TLB(set3, tag03 -> PPN 0D), Cache(5, tag 0D): [36,72,F0,1D] */
        { 0x3D4, 0x36, "TLB hit(set3,tag03->0D), Cache hit(5,0D) CO=0" },
        { 0x3D5, 0x72, "TLB hit(set3,tag03->0D), Cache hit(5,0D) CO=1" },
        { 0x3D6, 0xF0, "TLB hit(set3,tag03->0D), Cache hit(5,0D) CO=2" },
        { 0x3D7, 0x1D, "TLB hit(set3,tag03->0D), Cache hit(5,0D) CO=3" },

        /* TLB(set0, tag09 -> PPN 0D), same cache line as above */
        { 0x914, 0x36, "TLB hit(set0,tag09->0D), Cache hit(5,0D) CO=0" },
        { 0x915, 0x72, "TLB hit(set0,tag09->0D), Cache hit(5,0D) CO=1" },

        /* --- TLB miss + Page Table hit + Cache hit --- */
        /* PT(VPN 08 -> PPN 13), Cache(E, tag 13): [83,77,1B,D3] */
        { 0x238, 0x83, "TLB miss, PT hit(08->13), Cache hit(E,13) CO=0" },
        { 0x239, 0x77, "TLB miss, PT hit(08->13), Cache hit(E,13) CO=1" },
        { 0x23A, 0x1B, "TLB miss, PT hit(08->13), Cache hit(E,13) CO=2" },
        { 0x23B, 0xD3, "TLB miss, PT hit(08->13), Cache hit(E,13) CO=3" },

        /* PT(VPN 05 -> PPN 16), Cache(7, tag 16): [11,C2,DF,03] */
        { 0x15C, 0x11, "TLB miss, PT hit(05->16), Cache hit(7,16) CO=0" },
        { 0x15D, 0xC2, "TLB miss, PT hit(05->16), Cache hit(7,16) CO=1" },
        { 0x15E, 0xDF, "TLB miss, PT hit(05->16), Cache hit(7,16) CO=2" },
        { 0x15F, 0x03, "TLB miss, PT hit(05->16), Cache hit(7,16) CO=3" },

        /* PT(VPN 05 -> PPN 16), Cache(D, tag 16): [04,96,34,15] */
        { 0x174, 0x04, "TLB miss, PT hit(05->16), Cache hit(D,16) CO=0" },
        { 0x175, 0x96, "TLB miss, PT hit(05->16), Cache hit(D,16) CO=1" },
        { 0x176, 0x34, "TLB miss, PT hit(05->16), Cache hit(D,16) CO=2" },
        { 0x177, 0x15, "TLB miss, PT hit(05->16), Cache hit(D,16) CO=3" },

        /* --- TLB hit + Cache miss --- */
        /* TLB(set0, tag07 -> PPN 02), PA=0x080, CI=0, CT=02, cache[0] tag=19 != 02 */
        { 0x700, UNDETERMINED, "TLB hit(set0,tag07->02), Cache miss(0,tag19!=02)" },
        /* TLB(set3, tag0A -> PPN 34), PA=0xD00, CI=0, CT=34, cache[0] tag=19 != 34 */
        { 0xAC0, UNDETERMINED, "TLB hit(set3,tag0A->34), Cache miss(0,tag19!=34)" },

        /* --- TLB miss + Page Table hit + Cache miss --- */
        /* PT(VPN 02 -> PPN 33), PA=0xCCC, CI=3, cache[3] not loaded */
        { 0x08C, UNDETERMINED, "TLB miss, PT hit(02->33), Cache miss(set3 empty)" },
        /* PT(VPN 00 -> PPN 28), PA=0xA00, CI=0, CT=28, cache[0] tag=19 != 28 */
        { 0x000, UNDETERMINED, "TLB miss, PT hit(00->28), Cache miss(0,tag19!=28)" },
        /* PT(VPN 03 -> PPN 02), PA=0x080, CI=0, CT=02, cache[0] tag=19 != 02 */
        { 0x0C0, UNDETERMINED, "TLB miss, PT hit(03->02), Cache miss(0,tag19!=02)" },
        /* PT(VPN 09 -> PPN 17), PA=0x5C0, CI=0, CT=17, cache[0] tag=19 != 17 */
        { 0x240, UNDETERMINED, "TLB miss, PT hit(09->17), Cache miss(0,tag19!=17)" },

        /* --- TLB miss + Page Table miss (no translation) --- */
        /* VPN 01 not in page table */
        { 0x040, UNDETERMINED, "TLB miss, PT miss(VPN 01 not present)" },
        /* VPN 04 not in page table */
        { 0x100, UNDETERMINED, "TLB miss, PT miss(VPN 04 not present)" },
    };

    int num_tests = sizeof(tests) / sizeof(tests[0]);

    if (argc < 2) {
        fprintf(stderr, "Usage: %s <input_file>\n", argv[0]);
        return 1;
    }

    if (load_data(argv[1]) != 0)
        return 1;

    printf("=== Project4 Test Harness (%d cases) ===\n\n", num_tests);

    for (i = 0; i < num_tests; i++) {
        result = lookup(tests[i].va);

        if (result == tests[i].expected) {
            if (result == UNDETERMINED)
                printf("  PASS #%2d: VA=0x%03X -> Can not be determined  [%s]\n",
                       i + 1, tests[i].va, tests[i].desc);
            else
                printf("  PASS #%2d: VA=0x%03X -> %02X  [%s]\n",
                       i + 1, tests[i].va, result, tests[i].desc);
            pass++;
        } else {
            printf("  FAIL #%2d: VA=0x%03X -> got ", i + 1, tests[i].va);
            if (result == UNDETERMINED)
                printf("'Can not be determined'");
            else
                printf("'%02X'", result);
            printf(", expected ");
            if (tests[i].expected == UNDETERMINED)
                printf("'Can not be determined'");
            else
                printf("'%02X'", tests[i].expected);
            printf("  [%s]\n", tests[i].desc);
            fail++;
        }
    }

    printf("\n=== Results: %d passed, %d failed out of %d ===\n",
           pass, fail, pass + fail);

    return fail > 0 ? 1 : 0;
}
